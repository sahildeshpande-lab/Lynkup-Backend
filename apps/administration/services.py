from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException, status
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.accounts.db_models import User , UserRole , Role
from common.enums import EducationLevel, OnboardingStatus, UserStatus, RegistrationType
from common.pagination import build_paginated_response

from .schemas import (
    AdminUserActionRequest,
    ChangePasswordRequest,
    AdminSignupRequest,
    AdminEditProfileRequest,
    AdminLoginRequest,
    AdminForgotPasswordRequest,
    AdminResetPasswordRequest,
    AdminUserStatus,
)
from apps.accounts.schemas import ApiResponse, EmailSignupRequest, RefreshTokenRequest
from apps.accounts.services import JWT_ALGORITHM, JWT_SECRET, _generate_tokens
from apps.profiles.services import build_user_base_response
from apps.profiles.db_models import Profile
from sqlalchemy.orm import selectinload

import secrets
import string

PASSWORD_HASHER = PasswordHash((BcryptHasher(),))
ADMIN_MANAGED_ROLES = ["user", "moderator", "viewer"]

def _generate_temporary_password() -> str:
    alphabet = string.ascii_letters + string.digits + "@#$%"
    temp_password = "".join(
        secrets.choice(alphabet)
        for _ in range(12)
    )
    return temp_password


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

async def admin_complete_onboarding(
    user_id: UUID,
    bio: str,
    major: str,
    minor: str | None,
    university_id: str,
    education_level_id: int,
    academic_interests: str,
    profile_photo,
    db: AsyncSession,
) -> dict:
    import json
    import uuid

    from core.images import generate_download_url, normalize_image_name, save_image
    from apps.profiles.services import (
        _resolve_academic_interest_ids,
        build_user_base_response,
        calculate_completeness_score,
    )

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    profile = (await db.execute(select(Profile).where(Profile.user_id == user_id))).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user_id, first_name="", last_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    if profile_photo and profile_photo.filename:
        content = await profile_photo.read()
        if content:
            ext = profile_photo.filename.split(".")[-1] if "." in profile_photo.filename else "png"
            file_name = f"profiles/{uuid.uuid4()}.{ext}"
            save_image(
                file_name=file_name,
                content=content,
                content_type=profile_photo.content_type or "image/png",
            )
            profile.profile_photo_url = normalize_image_name(file_name)

    profile.bio = bio
    if university_id:
        try:
            profile.university_id = UUID(str(university_id))
        except ValueError:
            pass
    profile.major = major
    profile.minor = minor
    try:
        education_level = EducationLevel.from_id(education_level_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid education_level_id"
        ) from exc
    profile.edu_level = education_level.value

    if academic_interests is not None:
        val = academic_interests.strip()
        if val.startswith("[") and val.endswith("]"):
            try:
                interests_list = json.loads(val)
            except Exception:
                interests_list = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        else:
            interests_list = [x.strip() for x in val.split(",") if x.strip()]
        profile.profile_interests_id = await _resolve_academic_interest_ids(interests_list, db)

    user.onboarding_status = OnboardingStatus.completed
    db.add(user)
    db.add(profile)
    await db.flush()

    profile.completeness_score = await calculate_completeness_score(user_id, db)
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    user_data = await build_user_base_response(user, profile, db)
    if profile.profile_photo_url:
        user_data["profilePhoto_url"] = generate_download_url(profile.profile_photo_url)
    return {"user": user_data}


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


def _coerce_uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


async def _fetch_users_with_details(
    db: AsyncSession,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
    role_name: str | None = "user",
) -> list[dict]:
    from apps.profiles.db_models.university_db_model import University
    from apps.profiles.db_models.country_db_model import Country
    from apps.profiles.db_models.academic_interests_db_model import AcademicInterest

    stmt = (
        select(User, Profile, University.name.label("university_name"), Country.name.label("country_name"))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .outerjoin(Country, Country.id == Profile.country_id)
        .where(User.is_deleted.is_(False))
    )

    if role_name:
        stmt = stmt.where(Role.name == role_name)

    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )

    stmt = (
        stmt.options(selectinload(User.roles))
        .order_by(User.created_at.desc())
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    results = (await db.execute(stmt)).all()

    # Pre-fetch academic interests in bulk to avoid N+1 query
    interest_ids = set()
    for row in results:
        profile = row.Profile
        if profile and profile.profile_interests_id:
            for value in profile.profile_interests_id:
                if value is None:
                    continue
                try:
                    interest_ids.add(int(value))
                except (TypeError, ValueError):
                    pass

    interest_name_map = {}
    if interest_ids:
        interest_stmt = select(AcademicInterest.id, AcademicInterest.name).where(
            AcademicInterest.id.in_(list(interest_ids))
        )
        interest_rows = (await db.execute(interest_stmt)).all()
        interest_name_map = {r.id: r.name for r in interest_rows}

    items = []
    for row in results:
        user = row.User
        profile = row.Profile
        univ_name = row.university_name
        cntry_name = row.country_name

        # Map interests for this profile
        profile_interests = []
        if profile and profile.profile_interests_id:
            for value in profile.profile_interests_id:
                if value is None:
                    continue
                try:
                    interest_id = int(value)
                except (TypeError, ValueError):
                    continue
                if interest_id in interest_name_map:
                    profile_interests.append(interest_name_map[interest_id])

        user_data = await build_user_base_response(
            user,
            profile,
            db,
            university_name=univ_name,
            country_name=cntry_name,
            interests=profile_interests,
        )
        items.append(user_data)

    return items


async def list_users(
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    search: str | None = None,
) -> dict:
    from apps.profiles.db_models.university_db_model import University

    count_stmt = (
        select(func.count(User.id))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(User.is_deleted.is_(False), Role.name == "user")
    )
    if search:
        pattern = f"%{search}%"
        count_stmt = count_stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )
    total_items = int((await db.execute(count_stmt)).scalar_one())
    items = await _fetch_users_with_details(db, page, page_size, search=search,role="user")
    if page is None : 
        page=1 
    if page_size is None:
        page_size=len(items)
    
    return build_paginated_response(items, page, page_size, total_items).model_dump()

async def list_moderators(
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    search: str | None = None,
) -> dict:
    from apps.profiles.db_models.university_db_model import University

    count_stmt = (
        select(func.count(User.id))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(User.is_deleted.is_(False), Role.name == "moderator")
    )
    if search:
        pattern = f"%{search}%"
        count_stmt = count_stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )
    total_items = int((await db.execute(count_stmt)).scalar_one())
    items = await _fetch_users_with_details(db, page, page_size, search=search,role="moderator",)
    if page is None : 
        page=1 
    if page_size is None:
        page_size=len(items)
    
    return build_paginated_response(items, page, page_size, total_items).model_dump()

async def list_viewer(
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    search: str | None = None,
) -> dict:
    from apps.profiles.db_models.university_db_model import University

    count_stmt = (
        select(func.count(User.id))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(User.is_deleted.is_(False), Role.name == "viewer")
    )
    if search:
        pattern = f"%{search}%"
        count_stmt = count_stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )
    total_items = int((await db.execute(count_stmt)).scalar_one())
    items = await _fetch_users_with_details(db, page, page_size, search=search, role="viewer",)
    if page is None : 
        page=1 
    if page_size is None:
        page_size=len(items)
    
    return build_paginated_response(items, page, page_size, total_items).model_dump()

async def _fetch_users_with_details(
    db: AsyncSession,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
    role:str | None = None ,
) -> list[dict]:
    from apps.profiles.db_models.university_db_model import University
    from apps.profiles.db_models.country_db_model import Country
    from apps.profiles.db_models.academic_interests_db_model import AcademicInterest

    stmt = (
        select(User, Profile, University.name.label("university_name"), Country.name.label("country_name"))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .outerjoin(Country, Country.id == Profile.country_id)
        .where(User.is_deleted.is_(False), Role.name == role)
    )
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )
    stmt = (
        stmt.options(selectinload(User.roles))
        .order_by(User.created_at.desc())
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    results = (await db.execute(stmt)).all()

    # Pre-fetch academic interests in bulk to avoid N+1 query
    interest_ids = set()
    for row in results:
        profile = row.Profile
        if profile and profile.profile_interests_id:
            for value in profile.profile_interests_id:
                if value is None:
                    continue
                try:
                    interest_ids.add(int(value))
                except (TypeError, ValueError):
                    pass

    interest_name_map = {}
    if interest_ids:
        interest_stmt = select(AcademicInterest.id, AcademicInterest.name).where(
            AcademicInterest.id.in_(list(interest_ids))
        )
        interest_rows = (await db.execute(interest_stmt)).all()
        interest_name_map = {r.id: r.name for r in interest_rows}

    items = []
    for row in results:
        user = row.User
        profile = row.Profile
        univ_name = row.university_name
        cntry_name = row.country_name

        # Map interests for this profile
        profile_interests = []
        if profile and profile.profile_interests_id:
            for value in profile.profile_interests_id:
                if value is None:
                    continue
                try:
                    interest_id = int(value)
                except (TypeError, ValueError):
                    continue
                if interest_id in interest_name_map:
                    profile_interests.append(interest_name_map[interest_id])

        user_data = await build_user_base_response(
            user,
            profile,
            db,
            university_name=univ_name,
            country_name=cntry_name,
            interests=profile_interests,
        )
        items.append(user_data)

    return items


async def export_users(page: int | None, page_size: int | None, db: AsyncSession) -> dict:
    if page is not None and page_size is not None:
        total_items = int((await db.execute(select(func.count(User.id)).join(UserRole,UserRole.user_id == User.id).join(Role,Role.id == UserRole.role_id).where(User.is_deleted.is_(False),Role.name=="users"))).scalar_one())
        items = await _fetch_users_with_details(db, page, page_size,role="user")
        return build_paginated_response(items, page, page_size, total_items).model_dump()
    else:
        # Default case: list all users
        items = await _fetch_users_with_details(db,role="user")
        total_items = len(items)
        return build_paginated_response(items, 1, max(total_items, 1), total_items).model_dump()


async def admin_create_user(payload: AdminUserCreateRequest, db: AsyncSession) -> ApiResponse:
    from apps.accounts.services import assign_user_role
    from core.auth.services import create_firebase_user, delete_firebase_user
    from core.email_service import send_temporary_password_email

    email = payload.email.lower()
    role_name = payload.role.value if hasattr(payload.role, "value") else str(payload.role)
    if role_name not in ADMIN_MANAGED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="role must be one of: user, moderator, viewer",
        )

    existing_user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing_user:
        return ApiResponse(status=False, message="Email already registered", data=None)

    temporary_password = _generate_temporary_password()
    firebase_uid: str | None = None

    if role_name == "user":
        display_name = f"{payload.firstName} {payload.lastName}".strip()
        try:
            firebase_user = create_firebase_user(
                email=email,
                password=temporary_password,
                display_name=display_name or None,
            )
            firebase_uid = getattr(firebase_user, "uid", None)
            if not firebase_uid and isinstance(firebase_user, dict):
                firebase_uid = firebase_user.get("uid")
            if not firebase_uid:
                raise RuntimeError("Firebase user response did not include uid")
        except Exception as exc:
            logger.exception("Failed to create Firebase user for %s", email)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to create Firebase user",
            ) from exc

    user_status = UserStatus.active if role_name in ("moderator", "viewer") else UserStatus.pending

    now = datetime.now(timezone.utc)
    user = User(
        firebase_uid=firebase_uid,
        email=email,
        password_hash=PASSWORD_HASHER.hash(temporary_password),
        registration_type=RegistrationType.email,
        status=user_status,
        onboarding_status=OnboardingStatus.not_started,
        created_at=now,
        updated_at=now,
        email_verified_at=now,
    )

    try:
        db.add(user)
        await db.flush()

        await assign_user_role(db, user, role_name)

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
        except Exception:
            logger.exception("Failed to calculate profile completeness for admin-created user %s", user.id)

        await db.commit()
    except Exception:
        await db.rollback()
        if firebase_uid:
            try:
                delete_firebase_user(firebase_uid)
            except Exception:
                logger.exception("Failed to roll back Firebase user %s after local create failure", firebase_uid)
        raise

    await db.refresh(user)
    await db.refresh(profile)

    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()
    user_data = await build_user_base_response(user, profile, db)

    full_name = f"{payload.firstName} {payload.lastName}".strip()
    email_sent = await send_temporary_password_email(
        email,
        temporary_password,
        full_name=full_name or None,
        role=role_name,
    )

    return ApiResponse(
        status=True,
        message="User created successfully",
        data={
            "user": user_data,
            "emailSent": email_sent,
            "authProvider": "firebase" if role_name == "user" else "local",
        },
    )


async def admin_get_user(user_id: str, db: AsyncSession) -> dict:
    user_uuid = _coerce_uuid(user_id)
    user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == user_uuid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    return {"user": await build_user_base_response(user, profile, db)}


async def admin_delete_users(user_ids: list[str], db: AsyncSession) -> dict:
    from core.auth.services import revoke_firebase_tokens
    deleted_users = []
    now = datetime.now(timezone.utc)
    for user_id in user_ids:
        try:
            user_uuid = _coerce_uuid(user_id)
        except Exception:
            continue
        user = (await db.execute(select(User).where(User.id == user_uuid))).scalar_one_or_none()
        if user is None:
            continue
        user.status = UserStatus.deleting
        user.is_deleted = True
        user.deleted_at = now
        user.purge_after = now + timedelta(days=1)
        db.add(user)
        
        if user.firebase_uid and not user.firebase_uid.startswith("admin-"):
            try:
                revoke_firebase_tokens(user.firebase_uid)
            except Exception:
                pass
        profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
        user_data = await build_user_base_response(user, profile, db)
        deleted_users.append({
            "deleted": True,
            "status": user.status.value if hasattr(user.status, "value") else str(user.status),
            "deleted_at": user.deleted_at,
            "purge_after": user.purge_after,
            "user": user_data,
        })
    await db.commit()
    return {"deleted_users": deleted_users}

async def admin_edit_profile(
    user_id: str,
    payload: AdminEditProfileRequest,
    db: AsyncSession,
):
    from apps.profiles.db_models.profile_db_model import Profile
    from core.images import (
        file_exists,
        normalize_image_name,
        generate_download_url,
    )

    stmt = select(Profile).where(Profile.user_id == user_id)
    profile = (await db.execute(stmt)).scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found"
        )

    profile.first_name = payload.firstName
    profile.last_name = payload.lastName

    photo_key = payload.profilePhotoKey or payload.profile_photo_key

    # Update photo if supplied
    if photo_key:

        if not file_exists(photo_key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="profile_photo_key does not reference an uploaded file"
            )

        profile.profile_photo_url = normalize_image_name(
            photo_key
        )

    db.add(profile)

    stmt = (
        select(User)
        .options(selectinload(User.roles))
        .where(User.id == user_id)
    )
    user = (await db.execute(stmt)).scalar_one()

    await db.commit()
    await db.refresh(profile)

    user_data = await build_user_base_response(user, profile, db)
    return ApiResponse(
        status=True,
        message="Profile updated successfully",
        data={
            "user": user_data,
            "emailSent": False,
        },
    )

async def admin_update_user_status(
    user_id: str,
    status: AdminUserStatus,
    db: AsyncSession
) -> dict:
    from core.auth.services import (
        disable_firebase_user,
        enable_firebase_user
    )

    user_uuid = _coerce_uuid(user_id)

    user = (
        await db.execute(
            select(User)
            .options(selectinload(User.roles))
            .where(User.id == user_uuid)
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    # Check BEFORE updating
    already_same_status = user.status == status

    if not already_same_status:
        user.status = status
        user.updated_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(user)

        firebase_error = None

        if user.firebase_uid:
            try:
                if status == AdminUserStatus.active:
                    enable_firebase_user(user.firebase_uid)
                else:
                    disable_firebase_user(user.firebase_uid)
            except Exception as e:
                firebase_error = str(e)
    else:
        firebase_error = None

    profile = (
        await db.execute(
            select(Profile)
            .where(Profile.user_id == user.id)
        )
    ).scalar_one_or_none()

    response = {
        "status": status.value,
        "already_exists": already_same_status,
        "user": await build_user_base_response(
            user,
            profile,
            db
        )
    }

    if firebase_error:
        response["firebase_error"] = firebase_error

    return response

async def admin_forgot_password(payload: AdminForgotPasswordRequest, db: AsyncSession) -> ApiResponse:
    from core.email_service import send_reset_password_email
    from apps.accounts.db_models import PasswordResetToken
    from core.auth.config import settings as auth_settings
    import os
    import uuid

    email = payload.email.lower()
    stmt = select(User).options(selectinload(User.roles)).where(User.email == email)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or user.role not in ("admin", "superadmin"):
        return ApiResponse(status=False, message="User not found", data=None)

    # Check for recent active token to rate limit
    now = datetime.now(timezone.utc)
    existing_stmt = select(PasswordResetToken).where(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at == None,
        PasswordResetToken.expires_at > now
    )
    existing_token = (await db.execute(existing_stmt)).scalar_one_or_none()
    if existing_token:
        return ApiResponse(
            status=False,
            message=f"Recently email for resest password as been send please try after {auth_settings.password_reset_token_expire_minutes} mins  ",
            data=None
        )

    # Generate token
    token_val = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=auth_settings.password_reset_token_expire_minutes)

    reset_token = PasswordResetToken(
        user_id=user.id,
        token=token_val,
        expires_at=expires_at,
    )
    db.add(reset_token)
    await db.commit()

    # Get application link
    app_link = os.getenv("APPLICATION_LINK", "https://frontend-domain.com/").rstrip("/") + "/"
    reset_link = f"{app_link}reset-password?token={token_val}"

    # Send email
    await send_reset_password_email(email, reset_link)

    return ApiResponse(status=True, message="Password reset link sent successfully to your mail ", data=None)


async def admin_reset_password(payload: AdminResetPasswordRequest, db: AsyncSession) -> ApiResponse:
    from apps.accounts.db_models import PasswordResetToken
    import uuid
    now=datetime.now(timezone.utc)

    if payload.token  :
        try:
            # Check if it is a valid UUID string
            token_uuid = uuid.UUID(payload.token)
        except ValueError:
            return ApiResponse(status=False, message="Invalid token format", data=None)

        stmt = select(PasswordResetToken).where(
            PasswordResetToken.token == str(token_uuid),
            PasswordResetToken.used_at == None
        )
        reset_token = (await db.execute(stmt)).scalar_one_or_none()
        if not reset_token:
            return ApiResponse(status=False, message="Invalid reset password link", data=None)

        now = datetime.now(timezone.utc)
        if reset_token.expires_at.replace(tzinfo=timezone.utc) < now:
            return ApiResponse(status=False, message="Your reset password link has expired.", data=None)

        user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == reset_token.user_id))).scalar_one_or_none()
        if not user or user.role not in ("admin", "superadmin"):
            return ApiResponse(status=False, message="User not found", data=None)

        user.password_hash = PASSWORD_HASHER.hash(payload.new_password)
        user.updated_at = now
        db.add(user)

        # Invalidate token
        reset_token.used_at = now
        db.add(reset_token)
        await db.commit()

        return ApiResponse(status=True, message="Password reset successful", data=None)

async def change_password(
    payload: ChangePasswordRequest,
    current_user: User,
    db: AsyncSession
) -> ApiResponse:

    if not current_user.password_hash or not PASSWORD_HASHER.verify(payload.current_password, current_user.password_hash):
        return ApiResponse(
            status=False,
            message="existing password does not match",
            data=None
        )

    current_user.password_hash = PASSWORD_HASHER.hash(
        payload.new_password
    )

    current_user.updated_at = datetime.now(timezone.utc)

    await db.commit()

    return ApiResponse(
        status=True,
        message="Password updated successfully",
        data=None
    )

