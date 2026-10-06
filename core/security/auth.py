from __future__ import annotations

from datetime import timedelta

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
import jwt

ACCESS_TOKEN_TTL = timedelta(minutes=auth_settings.access_token_expire_minutes)

bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    bearerFormat="JWT",
    description="Send the Kampulynk access token as: Bearer <token>",
    auto_error=False,
)


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


async def _authenticate_admin_on_common_route(
    request: Request,
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> User:
    """Admin branch for shared routes: optional X-Client-Type + admin JWT + RSA signing.

    App-user callers never enter this path — they use Firebase / user JWT only.
    """
    from apps.administration.services.signing_service import verify_signed_admin_request
    from core.request_signing import require_web_client_type

    # Soft when CLIENT_TYPE_ENFORCE=false; encrypted "web" required when enforce+key set.
    require_web_client_type(request)
    admin = await get_current_admin(request, credentials, db)
    session_id = getattr(request.state, "admin_session_id", None)
    if session_id is None:
        raise ApiError("Session expired. Please sign in again.")
    return await verify_signed_admin_request(
        request,
        admin,
        db,
        session_id=session_id,
        skip_origin=False,
    )


async def get_current_user_or_superadmin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Shared routes: app users via Firebase/user JWT; superadmin via client-type + RSA."""
    if not credentials:
        raise ApiError("Missing access token")

    try:
        decoded = jwt.decode(
            credentials.credentials,
            auth_settings.jwt_secret,
            algorithms=[auth_settings.jwt_algorithm],
        )
        if decoded.get("type") == "access":
            stmt = select(User).options(selectinload(User.roles)).where(User.id == decoded.get("sub"))
            user = (await db.execute(stmt)).scalar_one_or_none()
            if user is not None:
                if user.role == "superadmin":
                    return await _authenticate_admin_on_common_route(request, credentials, db)
                if user.role == "user":
                    _ensure_active_user(user)
                    return user
                raise ApiError("Insufficient permissions")
    except ApiError:
        raise
    except Exception:  # nosec B110 -- fall through to generic user auth
        pass

    user = await get_current_user(credentials, db)
    if user.role == "user":
        return user
    if user.role == "superadmin":
        raise ApiError("Session expired. Please sign in again.")
    raise ApiError("Insufficient permissions")


async def get_current_user_moderator_or_superadmin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    """Shared routes: app users via Firebase/user JWT; staff via client-type + RSA."""
    if not credentials:
        raise ApiError("Missing access token")

    staff_roles = ("moderator", "viewer", "superadmin")
    try:
        decoded = jwt.decode(
            credentials.credentials,
            auth_settings.jwt_secret,
            algorithms=[auth_settings.jwt_algorithm],
        )
        if decoded.get("type") == "access":
            stmt = select(User).options(selectinload(User.roles)).where(User.id == decoded.get("sub"))
            user = (await db.execute(stmt)).scalar_one_or_none()
            if user is not None:
                if user.role in staff_roles:
                    admin = await _authenticate_admin_on_common_route(request, credentials, db)
                    if admin.role not in staff_roles:
                        raise ApiError("Insufficient permissions")
                    return admin
                if user.role == "user":
                    _ensure_active_user(user)
                    return user
                raise ApiError("Insufficient permissions")
    except ApiError:
        raise
    except Exception:  # nosec B110 -- fall through to generic user auth
        pass

    user = await get_current_user(credentials, db)
    if user.role == "user":
        return user
    if user.role in staff_roles:
        raise ApiError("Session expired. Please sign in again.")
    raise ApiError("Insufficient permissions")


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
