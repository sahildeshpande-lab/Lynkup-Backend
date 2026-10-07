from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
import hashlib
import logging
import jwt
from fastapi import HTTPException, status
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.accounts.db_models import SecurityEventType, User, UserRole
from apps.accounts.services.common_service import (
    get_role_from_db,
    _normalize_role_name,
    log_security_event,
)
from apps.administration.services.session_service import (
    create_admin_session,
    require_active_admin_session,
    require_admin_session_for_refresh,
    revoke_admin_session,
    set_admin_session_refresh_jti,
)
from common.enums import OnboardingStatus, UserStatus, RegistrationType, inactive_account_message
from common.exceptions import ApiError
from core.auth.config import settings as auth_settings
from ..schemas import AdminLoginRequest, AdminSignupRequest
from apps.accounts.schemas import ApiResponse, RefreshTokenRequest
from apps.accounts.services import JWT_ALGORITHM, JWT_SECRET
from apps.profiles.services import build_user_base_response
from apps.profiles.db_models import Profile

PASSWORD_HASHER = PasswordHash((BcryptHasher(),))

from .user_management_service import _coerce_uuid

logger = logging.getLogger(__name__)

ADMIN_AUTH_ALLOWED_ROLES = frozenset({"superadmin", "moderator", "viewer"})
ADMIN_ACCESS_FORBIDDEN_MESSAGE = "Forbidden: Admin access required"
GENERIC_LOGIN_FAILURE = "Incorrect Username or Password."


def _ensure_admin_role(role: str) -> None:
    if _normalize_role_name(role) not in ADMIN_AUTH_ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ADMIN_ACCESS_FORBIDDEN_MESSAGE,
        )


def _ensure_admin_account_active(user: User) -> None:
    """Reject deleted/suspended/banned admins with a 401 ApiError."""
    if user.status == UserStatus.deleting or user.deleted_at or getattr(user, "is_deleted", False):
        raise ApiError(inactive_account_message(UserStatus.deleting))
    if user.status in (UserStatus.suspended, UserStatus.banned):
        raise ApiError(inactive_account_message(user.status))


def _admin_password_fingerprint(password_hash: str | None) -> str:
    if not password_hash:
        return ""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def validate_admin_session_token(decoded: dict, user: User) -> None:
    """Reject admin JWTs issued before the current password was set."""
    expected = _admin_password_fingerprint(user.password_hash)
    token_pf = decoded.get("pf")
    if not token_pf or token_pf != expected:
        raise ApiError("Session expired. Please sign in again.")


def _client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = getattr(request, "headers", {}).get("x-forwarded-for") if hasattr(request, "headers") else None
    if forwarded:
        return str(forwarded).split(",")[0].strip() or None
    client = getattr(request, "client", None)
    if client is not None:
        return getattr(client, "host", None)
    return None


async def _log_admin_auth_event(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    event_type: SecurityEventType,
    request=None,
    metadata: dict | None = None,
) -> None:
    if user_id is None:
        return
    try:
        await log_security_event(
            db,
            user_id,
            event_type,
            event_metadata=metadata,
            ip_address=_client_ip(request),
        )
    except Exception:
        logger.exception("Failed to write admin auth security event")


def _generate_admin_tokens(
    user: User,
    *,
    session_id: UUID,
    refresh_jti: str | None = None,
) -> tuple[str, str, str]:
    """Mint admin access + refresh JWTs bound to an independent session UUID.

    Returns ``(access_token, refresh_token, refresh_jti)``.
    Persist the returned jti on ``admin_sessions.refresh_jti`` at login and refresh.
    """
    now = datetime.now(timezone.utc)
    access_minutes = max(1, int(auth_settings.admin_access_token_expire_minutes))
    refresh_minutes = max(1, int(auth_settings.admin_refresh_token_expire_minutes))
    access_expiry = now + timedelta(minutes=access_minutes)
    refresh_expiry = now + timedelta(minutes=refresh_minutes)
    password_fingerprint = _admin_password_fingerprint(user.password_hash)
    session_claim = str(session_id)
    jti = refresh_jti or uuid4().hex

    access_payload = {
        "sub": str(user.id),
        "uid": user.firebase_uid,
        "email": user.email,
        "role": user.role,
        "type": "access",
        "session_id": session_claim,
        "pf": password_fingerprint,
        "exp": int(access_expiry.timestamp()),
        "iat": int(now.timestamp()),
    }

    refresh_payload = {
        "sub": str(user.id),
        "uid": user.firebase_uid,
        "type": "refresh",
        "session_id": session_claim,
        "pf": password_fingerprint,
        "jti": jti,
        "exp": int(refresh_expiry.timestamp()),
        "iat": int(now.timestamp()),
    }

    access_token = jwt.encode(access_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    refresh_token = jwt.encode(refresh_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return access_token, refresh_token, jti


async def admin_me(
    current_user: User,
    db: AsyncSession,
) -> ApiResponse:

    stmt_profile = select(Profile).where(
        Profile.user_id == current_user.id
    )

    profile = (
        await db.execute(stmt_profile)
    ).scalar_one_or_none()

    user_data = await build_user_base_response(
        current_user,
        profile,
        db
    )

    return ApiResponse(
        status=True,
        message="Profile fetched successfully",
        data={
            "user": user_data,
            "emailSent": False,
        },
    )


async def admin_token(payload: RefreshTokenRequest, db: AsyncSession) -> dict:
    try:
        decoded = jwt.decode(payload.refreshToken, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token") from exc

    user_id = decoded.get("sub")
    if not user_id or decoded.get("type") not in (None, "refresh"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    jti = decoded.get("jti")
    if not jti:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = (
        await db.execute(select(User).options(selectinload(User.roles)).where(User.id == _coerce_uuid(user_id)))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    _ensure_admin_account_active(user)
    try:
        validate_admin_session_token(decoded, user)
        session = await require_admin_session_for_refresh(
            db,
            decoded.get("session_id"),
            user_id=user.id,
        )
    except ApiError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message) from exc

    if not session.refresh_jti or session.refresh_jti != jti:
        await revoke_admin_session(db, session, revoke_keys=True, emit_event=True)
        await _log_admin_auth_event(
            db,
            user_id=user.id,
            event_type=SecurityEventType.TOKEN_REVOKED,
            metadata={
                "session_id": str(session.id),
                "reason": "refresh_token_reuse",
            },
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    access_token, refresh_token, new_jti = _generate_admin_tokens(user, session_id=session.id)
    await set_admin_session_refresh_jti(db, session, new_jti)
    await db.commit()
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",  # nosec B105 -- token type constant, not a password
    }


async def admin_signin(
    payload: AdminLoginRequest,
    db: AsyncSession,
    *,
    request=None,
) -> ApiResponse:
    from apps.administration.services.signing_service import validate_origin
    from apps.administration.services.signing_store import consume_keyed_rate_limit

    if request is not None:
        validate_origin(request)

    email = payload.email.lower()
    ip = _client_ip(request) or "unknown"
    rate_key = f"login:{ip}:{email}"
    allowed = await consume_keyed_rate_limit(
        rate_key,
        limit=int(auth_settings.admin_signing_rate_limit_requests),
        window_seconds=int(auth_settings.admin_signing_rate_limit_window_seconds),
    )
    if not allowed:
        # Avoid enumeration: same message as bad credentials when no user id known.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
        )

    stmt = select(User).options(selectinload(User.roles)).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message=GENERIC_LOGIN_FAILURE, data=None)

    if not user.password_hash or not PASSWORD_HASHER.verify(payload.password, user.password_hash):
        await _log_admin_auth_event(
            db,
            user_id=user.id,
            event_type=SecurityEventType.LOGIN_FAILED,
            request=request,
            metadata={"reason": "invalid_credentials"},
        )
        await db.commit()
        return ApiResponse(status=False, message=GENERIC_LOGIN_FAILURE, data=None)

    try:
        _ensure_admin_role(user.role)
    except HTTPException:
        await _log_admin_auth_event(
            db,
            user_id=user.id,
            event_type=SecurityEventType.LOGIN_FAILED,
            request=request,
            metadata={"reason": "invalid_role"},
        )
        await db.commit()
        raise

    try:
        _ensure_admin_account_active(user)
    except ApiError as exc:
        await _log_admin_auth_event(
            db,
            user_id=user.id,
            event_type=SecurityEventType.LOGIN_FAILED,
            request=request,
            metadata={"reason": "inactive_account", "detail": exc.message},
        )
        await db.commit()
        raise

    refresh_jti = uuid4().hex
    session = await create_admin_session(db, user, refresh_jti=refresh_jti)

    bound_key_id = None
    if payload.keyId is not None:
        from apps.administration.services.signing_service import bind_signing_key_after_login

        bound_key_id = await bind_signing_key_after_login(
            db,
            user,
            payload.keyId,
            session_id=session.id,
            request=request,
        )

    access_token, refresh_token, _jti = _generate_admin_tokens(
        user,
        session_id=session.id,
        refresh_jti=refresh_jti,
    )

    await _log_admin_auth_event(
        db,
        user_id=user.id,
        event_type=SecurityEventType.LOGIN_SUCCESS,
        request=request,
        metadata={"session_id": str(session.id)},
    )
    await db.commit()

    profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    user_data = await build_user_base_response(user, profile, db)
    data = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "sessionId": str(session.id),
        "user": user_data,
        "emailSent": False,
    }
    if bound_key_id is not None:
        data["keyId"] = str(bound_key_id)
    return ApiResponse(
        status=True,
        message="Login successful",
        data=data,
    )


async def admin_logout(
    current_user: User,
    db: AsyncSession,
    *,
    request=None,
) -> ApiResponse:
    """Revoke the current admin browser session and any bound signing keys."""
    session = getattr(getattr(request, "state", None), "admin_session", None)
    if session is None:
        session_id = getattr(getattr(request, "state", None), "admin_session_id", None)
        session = await require_active_admin_session(
            db,
            session_id,
            user_id=current_user.id,
        )

    await revoke_admin_session(db, session, revoke_keys=True, emit_event=True)
    await db.commit()
    return ApiResponse(
        status=True,
        message="Logged out successfully",
        data={"sessionId": str(session.id)},
    )


async def admin_signup(
    payload: AdminSignupRequest,
    db: AsyncSession,
    *,
    current_user: User,
) -> ApiResponse:
    """Create a staff account. Caller must be an authenticated superadmin.

    Does not mint tokens for the new account (avoids switching the creator session).
    Only a superadmin may create another superadmin.
    """
    if _normalize_role_name(getattr(current_user, "role", "")) != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Superadmin access required",
        )

    email = payload.email.lower()

    from common.email_validation import validate_disposable_email

    try:
        validate_disposable_email(
            email,
            is_enabled=auth_settings.is_disposable_email_enabled,
        )
    except ApiError as exc:
        return ApiResponse(status=False, message=str(exc.message), data=None)

    stmt = select(User).where(User.email == email)
    existing_user = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user:
        return ApiResponse(status=False, message="Email already registered", data=None)

    role_str = _normalize_role_name(payload.role)
    _ensure_admin_role(role_str)

    # Only superadmin can create superadmin; other staff roles are allowed for superadmin creators.
    if role_str == "superadmin" and _normalize_role_name(current_user.role) != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Cannot create superadmin",
        )

    role_obj = await get_role_from_db(db, role_str)
    if role_obj is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ADMIN_ACCESS_FORBIDDEN_MESSAGE,
        )

    now = datetime.now(timezone.utc)
    user = User(
        email=email,
        password_hash=PASSWORD_HASHER.hash(payload.password),
        registration_type=RegistrationType.email,
        status=UserStatus.active,
        onboarding_status=OnboardingStatus.not_started,
        created_at=now,
        updated_at=now,
        email_verified_at=now,
    )
    db.add(user)
    await db.flush()

    db.add(UserRole(user_id=user.id, role_id=role_obj.id))
    await db.flush()

    profile = Profile(
        user_id=user.id,
        first_name=payload.firstName,
        last_name=payload.lastName,
        completeness_score=0,
        updated_at=now,
    )
    db.add(profile)
    await db.flush()

    from apps.profiles.services import calculate_completeness_score
    try:
        profile.completeness_score = await calculate_completeness_score(user.id, db)
        db.add(profile)
    except Exception:  # nosec B110 -- best-effort profile creation
        pass

    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    user_data = await build_user_base_response(user, profile, db)
    await _log_admin_auth_event(
        db,
        user_id=current_user.id,
        event_type=SecurityEventType.ROLE_CHANGED,
        metadata={
            "action": "admin_signup",
            "created_user_id": str(user.id),
            "role": role_str,
        },
    )
    await db.commit()

    return ApiResponse(
        status=True,
        message="Signup successful",
        data={
            "user": user_data,
            "emailSent": False,
        },
    )
