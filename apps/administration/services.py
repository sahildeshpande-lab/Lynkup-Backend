from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.accounts.db_models import User
from common.enums import OnboardingStatus, UserStatus, EducationLevel
from common.pagination import build_paginated_response

from .schemas import (
    AdminUserActionRequest,
    AdminUserUpdateRequest,
)
from apps.accounts.schemas import ApiResponse, EmailSignupRequest, LoginRequest, RefreshTokenRequest
from apps.profiles.schemas import EducationUpdateRequest
from apps.accounts.services import JWT_ALGORITHM, JWT_SECRET, _generate_tokens
from apps.profiles.services import build_user_base_response
from apps.profiles.db_models import Profile
from sqlalchemy.orm import selectinload


async def admin_signup(payload: EmailSignupRequest, db: AsyncSession):
    from apps.accounts.services import signup

    response = await signup(payload, db)
    if not response.status:
        return response

    user = (
        await db.execute(
            select(User).options(selectinload(User.roles)).where(User.email == payload.email.lower())
        )
    ).scalar_one_or_none()
    if user is None:
        return response

    profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    user_data = await build_user_base_response(user, profile, db)
    access_token, refresh_token = _generate_tokens(user)
    return ApiResponse(
        status=True,
        message=response.message,
        data={
            "user": user_data,
            "emailSent": response.data.get("emailSent", False) if response.data else False,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        },
    )


async def admin_education(payload: EducationUpdateRequest, db: AsyncSession) -> dict:
    _ = db
    return {
        "user_id": None,
        "education": {
            "universityId": payload.universityId,
            "major": payload.major,
            "minor": payload.minor,
            "educationLevel": payload.educationLevel,
            "graduationDate": payload.graduationDate.isoformat() if payload.graduationDate else None,
        },
    }


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

    access_token, _refresh_token = _generate_tokens(user)
    return {"access_token": access_token, "token_type": "bearer"}


async def admin_signin(payload: LoginRequest, db: AsyncSession) -> ApiResponse:
    stmt = select(User).options(selectinload(User.roles)).where(User.email == payload.email.lower())
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        return ApiResponse(status=False, message="Invalid credentials", data=None)

    if user.role != "superadmin":
        return ApiResponse(status=False, message="Forbidden: Admin access required", data=None)

    profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    user_data = await build_user_base_response(user, profile, db)
    access_token, refresh_token = _generate_tokens(user)
    return ApiResponse(
        status=True,
        message="Login successful",
        data={
            "user": user_data,
            "emailSent": False,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        },
    )


def _coerce_uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


async def list_users(page: int, page_size: int, db: AsyncSession) -> dict:
    total_items = int((await db.execute(select(func.count()).select_from(User))).scalar_one())
    stmt = (
        select(User)
        .options(selectinload(User.roles))
        .order_by(User.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    users = (await db.execute(stmt)).scalars().all()
    items = []
    for user in users:
        profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
        items.append(await build_user_base_response(user, profile, db))
    return build_paginated_response(items, page, page_size, total_items).model_dump()


async def admin_create_user(payload: EmailSignupRequest, db: AsyncSession):
    from apps.accounts.services import signup

    return await signup(payload, db)


async def admin_get_user(user_id: str, db: AsyncSession) -> dict:
    user_uuid = _coerce_uuid(user_id)
    user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == user_uuid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    return {"user": await build_user_base_response(user, profile, db)}


async def admin_update_user(user_id: str, payload: AdminUserUpdateRequest, db: AsyncSession) -> dict:
    from core.auth.services import revoke_firebase_tokens
    from apps.profiles.db_models import Profile
    from apps.profiles.services import build_user_base_response
    from apps.accounts.services import assign_user_role
    from apps.accounts.db_models import UserRole
    user_uuid = _coerce_uuid(user_id)
    user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == user_uuid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    updates = payload.model_dump(exclude_none=True)
    profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user.id, display_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    first_name = updates.pop("firstName", None)
    last_name = updates.pop("lastName", None)
    if first_name is not None or last_name is not None:
        current_parts = (profile.display_name or "").split(" ", 1)
        current_first = current_parts[0] if current_parts else ""
        current_last = current_parts[1] if len(current_parts) > 1 else ""
        profile.display_name = f"{first_name if first_name is not None else current_first} {last_name if last_name is not None else current_last}".strip()

    if "email" in updates and updates["email"] is not None:
        user.email = updates["email"]
        updates.pop("email")

    if "university_id" in updates:
        university_id = updates.pop("university_id")
        try:
            profile.university_id = UUID(str(university_id)) if university_id else None
        except ValueError:
            profile.university_id = None
    if "major" in updates:
        profile.major = updates.pop("major")
    if "minor" in updates:
        profile.minor = updates.pop("minor")
    if "educationLevel" in updates:
        profile.edu_level = updates.pop("educationLevel")
    if "bio" in updates:
        profile.bio = updates.pop("bio")
    if "academicInterests" in updates:
        from apps.profiles.db_models.profile_interest_db_model import ProfileInterest
        interests = updates.pop("academicInterests") or []
        delete_stmt = select(ProfileInterest).where(ProfileInterest.profile_id == profile.id)
        old_interests = (await db.execute(delete_stmt)).scalars().all()
        for interest in old_interests:
            await db.delete(interest)
        await db.flush()
        for tag in interests:
            db.add(ProfileInterest(profile_id=profile.id, interest_tag=tag))
    if "graduationDate" in updates:
        graduation_date = updates.pop("graduationDate")
        profile.graduation_date = datetime.strptime(graduation_date, "%Y-%m").date() if graduation_date else None
    if "location" in updates:
        profile.location_text = updates.pop("location")

    for field_name, value in updates.items():
        if field_name == "status" and value is not None:
            user.status = UserStatus(value)
            if user.status in (UserStatus.suspended, UserStatus.banned, UserStatus.deleting):
                try:
                    revoke_firebase_tokens(user.firebase_uid)
                except Exception:
                    pass
            continue
        if field_name == "role" and value is not None:
            from apps.accounts.services import assign_user_role
            role_str = value.value if hasattr(value, "value") else str(value)
            await assign_user_role(db, user, role_str)
            continue
        setattr(user, field_name, value)
    user.updated_at = datetime.now(timezone.utc)
    db.add(user)
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)
    user_data = await build_user_base_response(user, profile, db)
    return {"updated": True, "user": user_data}


async def admin_delete_user(user_id: str, db: AsyncSession) -> dict:
    from core.auth.services import revoke_firebase_tokens
    user_uuid = _coerce_uuid(user_id)
    user = (await db.execute(select(User).where(User.id == user_uuid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.status = UserStatus.deleting
    now = datetime.now(timezone.utc)
    user.deleted_at = now
    user.purge_after = now + timedelta(days=1)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    try:
        revoke_firebase_tokens(user.firebase_uid)
    except Exception:
        pass
    return {
        "deleted": True,
        "status": user.status.value if hasattr(user.status, "value") else str(user.status),
        "deleted_at": user.deleted_at,
        "purge_after": user.purge_after,
    }


async def admin_suspend_user(payload: AdminUserActionRequest, db: AsyncSession) -> dict:
    from core.auth.services import revoke_firebase_tokens
    user_uuid = _coerce_uuid(payload.id)
    user = (await db.execute(select(User).where(User.id == user_uuid))).scalar_one_or_none()
    if user is None:
        return {"id": payload.id, "status": "not_found"}
    user.status = UserStatus.suspended
    user.updated_at = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    try:
        revoke_firebase_tokens(user.firebase_uid)
    except Exception:
        pass
    return {"id": payload.id, "status": "suspended"}


async def admin_ban_user(payload: AdminUserActionRequest, db: AsyncSession) -> dict:
    from core.auth.services import revoke_firebase_tokens
    user_uuid = _coerce_uuid(payload.id)
    user = (await db.execute(select(User).where(User.id == user_uuid))).scalar_one_or_none()
    if user is None:
        return {"id": payload.id, "status": "not_found"}
    user.status = UserStatus.banned
    user.updated_at = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    try:
        revoke_firebase_tokens(user.firebase_uid)
    except Exception:
        pass
    return {"id": payload.id, "status": "banned"}
