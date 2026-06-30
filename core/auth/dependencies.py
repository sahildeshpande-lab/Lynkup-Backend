from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Security, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from core.database.session import get_session

from .config import settings as auth_settings
from core.auth.services import verify_firebase_token


bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    bearerFormat="JWT",
    description="Send the Access  token as: Bearer <token>",
    auto_error=False,
)


def _credentials_or_401(
    credentials: HTTPAuthorizationCredentials | None,
) -> HTTPAuthorizationCredentials:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Firebase ID token",
        )
    return credentials


# async def get_current_firebase_user(
#     credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
# ) -> dict:
#     """Verify a Firebase ID token without a revocation network call."""
#     credentials = _credentials_or_401(credentials)
#     try:
#         return verify_firebase_token(credentials.credentials, check_revoked=False)
#     except Exception as exc:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid Firebase ID token",
#         ) from exc

async def get_current_firebase_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> dict:
    credentials = _credentials_or_401(credentials)

    try:
        return verify_firebase_token(
            credentials.credentials,
            check_revoked=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Firebase ID token",
        ) from exc


async def get_firebase_user_from_payload(
    request: Request,
) -> dict:
    token = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            token = (
                body.get("token_id")
                or body.get("tokenId")
                or body.get("idToken")
                or body.get("firebaseId")
                or body.get("firebase_id")
            )
    except Exception:
        pass

    if not token:
        # Fallback to header manually to avoid lock icon in Swagger
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Firebase ID token",
        )

    try:
        decoded = verify_firebase_token(
            token,
            check_revoked=False,
        )
        return decoded
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    
async def get_current_revoked_checked_firebase_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> dict:
    """Verify a Firebase ID token and check Firebase refresh-token revocation."""
    credentials = _credentials_or_401(credentials)
    try:
        return verify_firebase_token(credentials.credentials, check_revoked=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked Firebase ID token",
        ) from exc


async def require_recent_auth(
    firebase_user: dict = Security(get_current_revoked_checked_firebase_user),
) -> dict:
    """Require a revoked-checked token whose Firebase auth_time is recent."""
    auth_time = firebase_user.get("auth_time")
    if auth_time is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Recent Firebase authentication required",
        )

    now = int(datetime.now(timezone.utc).timestamp())
    if now - int(auth_time) > auth_settings.recent_auth_max_age_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Recent Firebase authentication required",
        )

    return firebase_user


async def get_current_user(
    firebase_user: dict = Security(get_current_firebase_user),
    db: AsyncSession = Depends(get_session),
):
    from apps.accounts.db_models import User
    from sqlmodel import select
    from apps.accounts.services import complete_firebase_registration

    from common.enums import UserStatus

    firebase_uid = firebase_user["uid"]
    stmt = select(User).where(User.firebase_uid == firebase_uid)
    existing_user = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user:
        if existing_user.deleted_at:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account deleted",
            )
        if existing_user.status in (UserStatus.suspended, UserStatus.banned):
            status_str = existing_user.status.value if hasattr(existing_user.status, "value") else str(existing_user.status)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Account is {status_str}",
            )

    return await complete_firebase_registration(firebase_user, db)


async def provision_user_from_firebase(firebase_user: dict, db):
    from apps.accounts.services import complete_firebase_registration

    return await complete_firebase_registration(firebase_user, db)


async def log_security_event(*args, **kwargs) -> None:
    from apps.accounts.services import log_security_event as _log_security_event

    await _log_security_event(*args, **kwargs)
