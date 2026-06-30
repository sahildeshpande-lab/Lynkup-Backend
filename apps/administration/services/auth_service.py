from __future__ import annotations
from datetime import datetime, timedelta, timezone
from uuid import UUID
import jwt
from fastapi import HTTPException, status
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from apps.accounts.db_models import User
from common.enums import EducationLevel, OnboardingStatus, UserStatus, RegistrationType
from ..schemas import  AdminLoginRequest
from apps.accounts.schemas import ApiResponse, RefreshTokenRequest
from apps.accounts.services import JWT_ALGORITHM, JWT_SECRET
from apps.profiles.services import build_user_base_response
from apps.profiles.db_models import Profile
from sqlalchemy.orm import selectinload
PASSWORD_HASHER = PasswordHash((BcryptHasher(),))

from .user_management_service import _coerce_uuid

def _generate_admin_tokens(user: User) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    access_expiry = now + timedelta(days=1)

    access_payload = {
        "sub": str(user.id),
        "uid": user.firebase_uid,
        "email": user.email,
        "role": user.role,
        "type": "access",
        "exp": int(access_expiry.timestamp()),
        "iat": int(now.timestamp()),
    }

    refresh_payload = {
        "sub": str(user.id),
        "uid": user.firebase_uid,
        "type": "refresh",
        "iat": int(now.timestamp()),
    }

    access_token = jwt.encode(access_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    refresh_token = jwt.encode(refresh_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return access_token, refresh_token

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

    user = (
        await db.execute(select(User).options(selectinload(User.roles)).where(User.id == _coerce_uuid(user_id)))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_token, _refresh_token = _generate_admin_tokens(user)
    return {"access_token": access_token, "token_type": "bearer"}

async def admin_signin(payload: AdminLoginRequest, db: AsyncSession) -> ApiResponse:
    stmt = select(User).options(selectinload(User.roles)).where(User.email == payload.email.lower())
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="User not found . Please sign up.", data=None)

    if not user.password_hash or not PASSWORD_HASHER.verify(payload.password, user.password_hash):
        return ApiResponse(status=False, message="Invalid credentials", data=None)

    if user.role == "user":
        return ApiResponse(status=False, message="Forbidden: Admin access required", data=None)

    profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    user_data = await build_user_base_response(user, profile, db)
    access_token, refresh_token = _generate_admin_tokens(user)
    return ApiResponse(
        status=True,
        message="Login successful",
        data={
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "user": user_data,
            "emailSent": False,
        },
    )
