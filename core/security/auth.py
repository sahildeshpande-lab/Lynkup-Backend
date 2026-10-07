from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Collection
from uuid import UUID

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from core.auth.config import settings as auth_settings
from core.database.session import get_session
from apps.accounts.db_models import User
from apps.accounts.services import complete_firebase_registration, AccountExistsException
from apps.accounts.services.common_service import (
    SOCIAL_EMAIL_MISMATCH_MESSAGE,
    firebase_email_matches_user,
)
from common.enums import UserStatus, inactive_account_message
from common.exceptions import ApiError
from core.request_signing import CLIENT_TYPE_MOBILE, CLIENT_TYPE_WEB, mark_client_type
import jwt

ACCESS_TOKEN_TTL = timedelta(minutes=auth_settings.access_token_expire_minutes)

_STAFF_ROLES = frozenset({"moderator", "viewer", "superadmin"})

bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    bearerFormat="JWT",
    description="Send the Kampulynk access token as: Bearer <token>",
    auto_error=False,
)


@dataclass(frozen=True, slots=True)
class AuthenticatedRequest:
    """Result of :func:`authenticate_request` for shared (web + mobile) routes."""

    client_type: str
    user_id: UUID
    role: str
    user: User


def get_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> str | None:
    if not credentials:
        return None
    return credentials.credentials


def _inactive_account_message(status: UserStatus) -> str:
    return inactive_account_message(status)


def _ensure_active_user(user: User) -> None:
    """Allow active and pending users; keep 401 for suspended/banned/deleting."""
    if (
        user.status == UserStatus.deleting
        or user.deleted_at
        or getattr(user, "is_deleted", False)
    ):
        raise ApiError(inactive_account_message(UserStatus.deleting))
    if user.status in (UserStatus.suspended, UserStatus.banned):
        raise ApiError(_inactive_account_message(user.status))
    if user.status not in (UserStatus.active, UserStatus.pending):
        raise ApiError(_inactive_account_message(user.status))


def _peek_local_access_claims(token: str) -> dict | None:
    """Return decoded local access JWT claims, or None if not a local access token."""
    try:
        decoded = jwt.decode(
            token,
            auth_settings.jwt_secret,
            algorithms=[auth_settings.jwt_algorithm],
        )
    except Exception:
        return None
    if decoded.get("type") != "access":
        return None
    return decoded


def _has_admin_jwt(claims: dict | None, role: str | None) -> bool:
    """True when Bearer looks like a web-admin access token (staff + session_id)."""
    if not claims or not role:
        return False
    return role in _STAFF_ROLES and bool(claims.get("session_id"))


async def _load_user_by_id(db: AsyncSession, user_id: str | None) -> User | None:
    if not user_id:
        return None
    stmt = select(User).options(selectinload(User.roles)).where(User.id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _authenticate_web_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> AuthenticatedRequest:
    """Web: admin JWT → session → Origin/timestamp/nonce/RSA signature."""
    from apps.administration.services.signing_service import verify_signed_admin_request

    admin = await get_current_admin(request, credentials, db)
    session_id = getattr(request.state, "admin_session_id", None)
    if session_id is None:
        raise ApiError("Session expired. Please sign in again.")

    verified = await verify_signed_admin_request(
        request,
        admin,
        db,
        session_id=session_id,
        skip_origin=False,
    )
    mark_client_type(request, CLIENT_TYPE_WEB)
    return AuthenticatedRequest(
        client_type=CLIENT_TYPE_WEB,
        user_id=verified.id,
        role=verified.role,
        user=verified,
    )


async def _verify_mobile_identity(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> User:
    """Resolve mobile/app user from local user JWT or Firebase ID token."""
    return await get_current_user(credentials, db)


async def _verify_mobile_request_proof(request: Request, user: User) -> None:
    """Mobile request proof: timestamp + nonce + HMAC.

    Not enforced yet — identity-only until mobile HMAC signing ships.
    """
    _ = (request, user)


async def _authenticate_mobile(
    request: Request,
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> AuthenticatedRequest:
    """Mobile: Firebase/user JWT (+ future timestamp/nonce/HMAC)."""
    user = await _verify_mobile_identity(credentials, db)
    if user.role != "user":
        # Staff must use the web admin JWT + RSA path on shared routes.
        raise ApiError("Session expired. Please sign in again.")
    await _verify_mobile_request_proof(request, user)
    mark_client_type(request, CLIENT_TYPE_MOBILE)
    return AuthenticatedRequest(
        client_type=CLIENT_TYPE_MOBILE,
        user_id=user.id,
        role=user.role,
        user=user,
    )


async def authenticate_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
    *,
    allowed_roles: Collection[str] | None = None,
) -> AuthenticatedRequest:
    """Single entrypoint for shared-route auth (web admin RSA vs mobile).

    Flow
    ----
    1. If Bearer is an admin access JWT (staff + ``session_id``):
       verify JWT → session → timestamp/nonce/RSA → ``client_type=web``.
    2. Else if Bearer is Firebase / app-user JWT:
       verify identity → (future HMAC) → ``client_type=mobile``.
    3. Otherwise reject.
    """
    if not credentials:
        raise ApiError("Missing access token")

    claims = _peek_local_access_claims(credentials.credentials)
    peeked_user: User | None = None
    if claims is not None:
        peeked_user = await _load_user_by_id(db, claims.get("sub"))

    if peeked_user is not None and _has_admin_jwt(claims, peeked_user.role):
        if allowed_roles is not None and peeked_user.role not in allowed_roles:
            raise ApiError("Insufficient permissions")
        auth = await _authenticate_web_admin(request, credentials, db)
    elif peeked_user is not None and peeked_user.role in _STAFF_ROLES:
        # Staff JWT without session_id cannot use the mobile branch.
        if allowed_roles is not None and peeked_user.role not in allowed_roles:
            raise ApiError("Insufficient permissions")
        raise ApiError("Session expired. Please sign in again.")
    elif peeked_user is not None and peeked_user.role == "user":
        _ensure_active_user(peeked_user)
        await _verify_mobile_request_proof(request, peeked_user)
        mark_client_type(request, CLIENT_TYPE_MOBILE)
        auth = AuthenticatedRequest(
            client_type=CLIENT_TYPE_MOBILE,
            user_id=peeked_user.id,
            role=peeked_user.role,
            user=peeked_user,
        )
    else:
        # Firebase / non-local Bearer → mobile identity path.
        auth = await _authenticate_mobile(request, credentials, db)

    if allowed_roles is not None and auth.role not in allowed_roles:
        raise ApiError("Insufficient permissions")
    return auth


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    if not credentials:
        raise ApiError("Missing access token")

    # Try local JWT decoding first
    try:
        decoded = jwt.decode(credentials.credentials, auth_settings.jwt_secret, algorithms=[auth_settings.jwt_algorithm])
        if decoded.get("type") == "access":
            stmt = select(User).options(selectinload(User.roles)).where(User.id == decoded.get("sub"))
            user = (await db.execute(stmt)).scalar_one_or_none()
            if user:
                _ensure_active_user(user)
                return user
    except ApiError:
        raise
    except Exception:  # nosec B110 -- best-effort auth fallback
        pass

    # Fallback to Firebase
    try:
        from core.auth.services import verify_firebase_token
        decoded = verify_firebase_token(credentials.credentials, check_revoked=True)
    except Exception as exc:
        raise ApiError("Invalid access token") from exc

    firebase_uid = decoded.get("uid")
    if not firebase_uid:
        raise ApiError("Invalid Firebase credentials")

    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        try:
            user = await complete_firebase_registration(decoded, db)
        except AccountExistsException as exc:
            raise ApiError(f"User already registered via {exc.registration_type}")
    elif not firebase_email_matches_user(decoded, user):
        raise ApiError(SOCIAL_EMAIL_MISMATCH_MESSAGE)

    _ensure_active_user(user)
    return user


async def get_current_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    if not credentials:
        raise ApiError("Missing access token")

    try:
        decoded = jwt.decode(credentials.credentials, auth_settings.jwt_secret, algorithms=[auth_settings.jwt_algorithm])
    except Exception as exc:
        raise ApiError("Invalid access token") from exc

    if decoded.get("type") != "access":
        raise ApiError("Invalid access token")

    stmt = select(User).options(selectinload(User.roles)).where(User.id == decoded.get("sub"))
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise ApiError("User not found")

    _ensure_active_user(user)

    if user.role in ("user",):
        raise ApiError("Insufficient permissions")

    from apps.administration.services.auth_service import validate_admin_session_token
    from apps.administration.services.session_service import (
        require_active_admin_session,
        touch_admin_session,
    )

    validate_admin_session_token(decoded, user)

    # Independent admin session UUID from JWT — never fall back to user.id.
    session = await require_active_admin_session(
        db,
        decoded.get("session_id"),
        user_id=user.id,
    )
    request.state.admin_session_id = session.id
    request.state.admin_session = session
    await touch_admin_session(db, session)

    return user


async def get_current_app_user(
    user: User = Depends(get_current_user),
) -> User:
    if user.role != "user":
        raise ApiError("Insufficient permissions")
    return user


async def get_current_user_or_superadmin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Shared routes: app users (mobile) or superadmin (web RSA)."""
    auth = await authenticate_request(
        request,
        credentials,
        db,
        allowed_roles=frozenset({"user", "superadmin"}),
    )
    return auth.user


async def get_current_user_moderator_or_superadmin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Shared routes: app users (mobile) or staff (web RSA)."""
    auth = await authenticate_request(
        request,
        credentials,
        db,
        allowed_roles=frozenset({"user", "moderator", "viewer", "superadmin"}),
    )
    return auth.user


async def get_current_moderator(
    user: User = Depends(get_current_admin),
) -> User:
    if user.role not in ("moderator", "superadmin"):
        raise ApiError("Insufficient permissions")
    return user


async def get_current_moderator_or_viewer(
    user: User = Depends(get_current_admin),
) -> User:
    if user.role not in ("moderator", "superadmin", "viewer"):
        raise ApiError("Insufficient permissions")
    return user


async def get_current_superadmin(
    user: User = Depends(get_current_admin),
) -> User:
    if user.role != "superadmin":
        raise ApiError("Insufficient permissions")
    return user
