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
from common.enums import OnboardingStatus, UserStatus, RegistrationType, inactive_account_message
from common.exceptions import ApiError
from ..schemas import AdminLoginRequest, AdminSignupRequest
from apps.accounts.schemas import ApiResponse, RefreshTokenRequest
from apps.accounts.services import JWT_ALGORITHM, JWT_SECRET
from apps.profiles.services import build_user_base_response
from apps.profiles.db_models import Profile
PASSWORD_HASHER = PasswordHash((BcryptHasher(),))

from .user_management_service import _coerce_uuid


def _ensure_admin_account_active(user: User) -> None:
    """Reject deleted/suspended/banned admins with a 401 ApiError."""
    if user.status == UserStatus.deleting or user.deleted_at or getattr(user, "is_deleted", False):
        raise ApiError(inactive_account_message(UserStatus.deleting))
    if user.status in (UserStatus.suspended, UserStatus.banned):
        raise ApiError(inactive_account_message(user.status))

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

    _ensure_admin_account_active(user)

    access_token, _refresh_token = _generate_admin_tokens(user)
    return {"access_token": access_token, "token_type": "bearer"}

async def admin_signin(payload: AdminLoginRequest, db: AsyncSession) -> ApiResponse:
    stmt = select(User).options(selectinload(User.roles)).where(User.email == payload.email.lower())
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="Incorrect Username or Password.", data=None)

    if not user.password_hash or not PASSWORD_HASHER.verify(payload.password, user.password_hash):
        return ApiResponse(status=False, message="Incorrect Username or Password.", data=None)

    if user.role == "user":
        return ApiResponse(status=False, message="Forbidden: Admin access required", data=None)

    _ensure_admin_account_active(user)

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

async def admin_signup(payload: AdminSignupRequest, db: AsyncSession):
    email = payload.email.lower()

    # Check duplicate email
    stmt = select(User).where(User.email == email)
    existing_user = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user:
        return ApiResponse(status=False, message="Email already registered", data=None)

    from apps.accounts.services import assign_user_role

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

    role_str = payload.role.value if hasattr(payload.role, "value") else str(payload.role)
    await assign_user_role(db, user, role_str)

    profile = Profile(
        user_id=user.id,
        first_name=payload.firstName,
        last_name=payload.lastName,
        completeness_score=0,
        updated_at=now
    )
    db.add(profile)
    await db.flush()

    from apps.profiles.services import calculate_completeness_score
    try:
        profile.completeness_score = await calculate_completeness_score(user.id, db)
        db.add(profile)
    except Exception:
        pass

    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()

    user_data = await build_user_base_response(user, profile, db)
    access_token, refresh_token = _generate_admin_tokens(user)
    return ApiResponse(
        status=True,
        message="Signup successful",
        data={
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "user": user_data,
            "emailSent": False,
        },
    )
