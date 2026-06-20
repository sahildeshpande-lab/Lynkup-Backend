from __future__ import annotations

from datetime import timedelta

from fastapi import Security, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from core.auth.config import settings as auth_settings
from core.database.session import get_session
from apps.accounts.db_models import User
from apps.accounts.services import complete_firebase_registration
from common.enums import UserStatus
import hashlib
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


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing access token")

    # Try local JWT decoding first
    try:
        decoded = jwt.decode(credentials.credentials, auth_settings.jwt_secret, algorithms=[auth_settings.jwt_algorithm])
        if decoded.get("type") == "access":
            stmt = select(User).options(selectinload(User.roles)).where(User.id == decoded.get("sub"))
            user = (await db.execute(stmt)).scalar_one_or_none()
            if user:
                if user.deleted_at:
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account deleted")
                if user.status != UserStatus.active:
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is not active")
                return user
    except HTTPException:
        raise
    except Exception:
        pass

    # Fallback to Firebase
    try:
        from core.auth.services import verify_firebase_token
        decoded = verify_firebase_token(credentials.credentials, check_revoked=False)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from exc

    firebase_uid = decoded.get("uid")
    if not firebase_uid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Firebase credentials")

    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        user = await complete_firebase_registration(decoded, db)

    if user.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deleted"
        )

    if user.status != UserStatus.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active",
        )

    return user


async def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_session),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing access token")

    try:
        decoded = jwt.decode(credentials.credentials, auth_settings.jwt_secret, algorithms=[auth_settings.jwt_algorithm])
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from exc

    if decoded.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    stmt = select(User).options(selectinload(User.roles)).where(User.id == decoded.get("sub"))
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if user.deleted_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deleted"
        )

    if user.status != UserStatus.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is not active",
        )

    if user.role not in ("admin", "superadmin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    return user


async def get_current_superadmin(
    user: User = Depends(get_current_admin),
) -> User:
    if user.role != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions"
        )
    return user
