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
    AdminSignupRequest,
    AdminLoginRequest,
)
from apps.accounts.schemas import ApiResponse, EmailSignupRequest, RefreshTokenRequest
from apps.accounts.services import JWT_ALGORITHM, JWT_SECRET, _generate_tokens
from apps.profiles.services import build_user_base_response
from apps.profiles.db_models import Profile
from sqlalchemy.orm import selectinload

PASSWORD_HASHER = PasswordHash((BcryptHasher(),))


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
        display_name=f"{payload.firstName} {payload.lastName}".strip(),
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


# async def admin_complete_onboarding(
#     user_id: UUID,
#     bio: str,
#     major: str,
#     minor: str | None,
#     university_id: str,
#     education_level: str,
#     academic_interests: str,
#     profile_photo: UploadFile,
#     db: AsyncSession,
# ) -> dict:
#     if db is not None:
#         from apps.profiles.db_models import Profile
#         from core.images import save_image, settings, generate_download_url, normalize_image_name
#         from uuid import UUID as pyUUID
#         import uuid
#         import json
#         from common.enums import OnboardingStatus
#         from apps.profiles.services import calculate_completeness_score, build_user_base_response
        
#         # Verify user exists
#         stmt = select(User).where(User.id == user_id)
#         user = (await db.execute(stmt)).scalar_one_or_none()
#         if not user:
#             raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        
#         # Fetch or create profile
#         profile_stmt = select(Profile).where(Profile.user_id == user_id)
#         profile = (await db.execute(profile_stmt)).scalar_one_or_none()
#         if not profile:
#             profile = Profile(user_id=user_id, display_name="", completeness_score=0)
#             db.add(profile)
#             await db.flush()
            
#         profile_data = {}

#         if profile_photo and profile_photo.filename:
#             content = await profile_photo.read()
#             if content:
#                 ext = profile_photo.filename.split(".")[-1] if "." in profile_photo.filename else "png"
#                 file_name = f"profiles/{uuid.uuid4()}.{ext}"
#                 save_image(
#                     file_name=file_name,
#                     content=content,
#                     content_type=profile_photo.content_type or "image/png"
#                 )
#                 profile.profile_photo_url = normalize_image_name(file_name)
#                 profile_data["profilePhotoUrl"] = generate_download_url(profile.profile_photo_url)

#         profile.bio = bio
#         profile_data["bio"] = bio

#         if university_id:
#             try:
#                 profile.university_id = pyUUID(str(university_id))
#             except ValueError:
#                 pass
#         profile_data["universityId"] = str(profile.university_id) if profile.university_id else None
                    
#         profile.major = major
#         profile.minor = minor
#         profile.edu_level = education_level
#         profile_data["educationLevel"] = education_level
        
#         if academic_interests is not None:
#             interests_list = []
#             val = academic_interests.strip()
#             if val.startswith("[") and val.endswith("]"):
#                 try:
#                     interests_list = json.loads(val)
#                 except Exception:
#                     interests_list = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
#             else:
#                 interests_list = [x.strip() for x in val.split(",") if x.strip()]

#             from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
#             from uuid import UUID

#             resolved_uuids = []
#             for tag in interests_list:
#                 tag_clean = tag.strip()
#                 if not tag_clean:
#                     continue
#                 is_uuid = False
#                 try:
#                     uuid_val = UUID(tag_clean)
#                     is_uuid = True
#                 except ValueError:
#                     pass
                
#                 if is_uuid:
#                     stmt_interest = select(AcademicInterest).where(AcademicInterest.id == uuid_val)
#                     interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
#                     if interest_rec:
#                         resolved_uuids.append(str(interest_rec.id))
#                 else:
#                     stmt_interest = select(AcademicInterest).where(AcademicInterest.name.ilike(tag_clean))
#                     interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
#                     if not interest_rec:
#                         interest_rec = AcademicInterest(name=tag_clean, is_active=True)
#                         db.add(interest_rec)
#                         await db.flush()
#                     resolved_uuids.append(str(interest_rec.id))

#             profile.profile_interests_id = resolved_uuids
            
#             profile_data["academicInterests"] = interests_list

#         user.onboarding_status = OnboardingStatus.completed
#         db.add(user)
#         db.add(profile)
#         await db.flush()
        
#         try:
#             profile.completeness_score = await calculate_completeness_score(user_id, db)
#             db.add(profile)
#         except Exception:
#             pass
            
#         await db.commit()
#         await db.refresh(user)
#         await db.refresh(profile)

#         user_data = await build_user_base_response(user, profile, db)
#         return {"user": user_data}
#     return {}


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
        profile = Profile(user_id=user_id, display_name="", completeness_score=0)
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

    if user.role != "superadmin":
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
        .where(User.is_deleted.is_(False), Role.name == "user")
        .options(selectinload(User.roles))
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


async def list_users(page: int, page_size: int, db: AsyncSession) -> dict:
    total_items = int((await db.execute(select(func.count(User.id)).join(UserRole,UserRole.user_id == User.id).join(Role,Role.id == UserRole.role_id).where(User.is_deleted.is_(False),Role.name=="user"))).scalar_one())
    items = await _fetch_users_with_details(db, page, page_size)
    return build_paginated_response(items, page, page_size, total_items).model_dump()


async def export_users(page: int | None, page_size: int | None, db: AsyncSession) -> dict:
    if page is not None and page_size is not None:
        total_items = int((await db.execute(select(func.count(User.id)).join(UserRole,UserRole.user_id == User.id).join(Role,Role.id == UserRole.role_id).where(User.is_deleted.is_(False),Role.name=="user"))).scalar_one())
        items = await _fetch_users_with_details(db, page, page_size)
        return build_paginated_response(items, page, page_size, total_items).model_dump()
    else:
        # Default case: list all users
        items = await _fetch_users_with_details(db)
        total_items = len(items)
        return build_paginated_response(items, 1, max(total_items, 1), total_items).model_dump()


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


async def admin_delete_user(user_id: str, db: AsyncSession) -> dict:
    from core.auth.services import revoke_firebase_tokens
    user_uuid = _coerce_uuid(user_id)
    user = (await db.execute(select(User).where(User.id == user_uuid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.status = UserStatus.deleting
    user.is_deleted = True
    now = datetime.now(timezone.utc)
    user.deleted_at = now
    user.purge_after = now + timedelta(days=1)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    if user.firebase_uid and not user.firebase_uid.startswith("admin-"):
        try:
            revoke_firebase_tokens(user.firebase_uid)
        except Exception:
            pass
    profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    user_data = await build_user_base_response(user, profile, db)
    return {
        "deleted": True,
        "status": user.status.value if hasattr(user.status, "value") else str(user.status),
        "deleted_at": user.deleted_at,
        "purge_after": user.purge_after,
        "user": user_data,
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
    if user.firebase_uid and not user.firebase_uid.startswith("admin-"):
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
    if user.firebase_uid and not user.firebase_uid.startswith("admin-"):
        try:
            revoke_firebase_tokens(user.firebase_uid)
        except Exception:
            pass
    return {"id": payload.id, "status": "banned"}


# async def admin_suspend_user(payload: AdminUserActionRequest, db: AsyncSession) -> dict:
#     from core.auth.services import revoke_firebase_tokens
#     user_uuid = _coerce_uuid(payload.id)
#     user = (await db.execute(select(User).where(User.id == user_uuid))).scalar_one_or_none()
#     if user is None:
#         return {"id": payload.id, "status": "not_found"}
#     user.status = UserStatus.suspended
#     user.updated_at = datetime.now(timezone.utc)
#     db.add(user)
#     await db.commit()
#     await db.refresh(user)
#     if user.firebase_uid and not user.firebase_uid.startswith("admin-"):
#         try:
#             revoke_firebase_tokens(user.firebase_uid)
#         except Exception:
#             pass
#     return {"id": payload.id, "status": "suspended"}


# async def admin_ban_user(payload: AdminUserActionRequest, db: AsyncSession) -> dict:
#     from core.auth.services import revoke_firebase_tokens
#     user_uuid = _coerce_uuid(payload.id)
#     user = (await db.execute(select(User).where(User.id == user_uuid))).scalar_one_or_none()
#     if user is None:
#         return {"id": payload.id, "status": "not_found"}
#     user.status = UserStatus.banned
#     user.updated_at = datetime.now(timezone.utc)
#     db.add(user)
#     await db.commit()
#     await db.refresh(user)
#     if user.firebase_uid and not user.firebase_uid.startswith("admin-"):
#         try:
#             revoke_firebase_tokens(user.firebase_uid)
#         except Exception:
#             pass
#     return {"id": payload.id, "status": "banned"}
