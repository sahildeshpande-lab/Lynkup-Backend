from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID, uuid4
from core.images import normalize_image_name
from apps.accounts.schemas import UserBaseResponse
from ..schemas import ProfileUpdateRequest, ProfileVisibilityRequest, UpdateProfileMeRequest, UpdateProfileRequest
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User
from common.enums import UserStatus, OnboardingStatus, EducationLevel

from .completeness_service import calculate_completeness_score
from .interest_service import _resolve_academic_interest_ids
from .response_service import build_user_base_response

def _now() -> datetime:
    return datetime.now(timezone.utc)

async def get_profile_me(user: User, db: AsyncSession) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select

    stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    user_data = await build_user_base_response(user, profile, db)
    return {"user": user_data}

async def update_profile_me(
    current_user: User,
    payload: UpdateProfileMeRequest,
    db: AsyncSession
) -> dict:
    from uuid import UUID
    from fastapi import HTTPException, status
    from sqlmodel import select
    from apps.profiles.db_models.profile_db_model import Profile

    stmt = select(Profile).where(
        Profile.user_id == current_user.id
    )

    profile = (
        await db.execute(stmt)
    ).scalar_one_or_none()

    if not profile:
        profile = Profile(
            user_id=current_user.id,
            first_name="",
            last_name="",
            completeness_score=0
        )
        db.add(profile)
        await db.flush()

    #
    # Name
    #
    first_name = payload.firstName
    last_name = payload.lastName

    if first_name is not None or last_name is not None:
        profile.first_name = first_name if first_name is not None else profile.first_name
        profile.last_name = last_name if last_name is not None else profile.last_name

    #
    # Bio
    #
    if payload.bio is not None:
        profile.bio = payload.bio

    #
    # Major
    #
    if payload.major is not None:
        profile.major = payload.major

    #
    # University
    #
    if payload.universityId is not None:
        try:
            profile.university_id = UUID(
                payload.universityId
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid universityId"
            )

    current_user.onboarding_status = (
        OnboardingStatus.completed
    )

    db.add(current_user)
    db.add(profile)

    await db.flush()

    profile.completeness_score = (
        await calculate_completeness_score(
            current_user.id,
            db
        )
    )

    db.add(profile)

    await db.commit()

    await db.refresh(current_user)
    await db.refresh(profile)

    try:
        import logging
        local_logger = logging.getLogger(__name__)
        from core.email_service import send_profile_updated_email
        full_name = f"{profile.first_name} {profile.last_name}".strip() or None
        await send_profile_updated_email(current_user.email, full_name)
        local_logger.info("Profile updated email queued for user ID %s", current_user.id)
    except Exception as e:
        import logging
        local_logger = logging.getLogger(__name__)
        local_logger.exception("Failed to queue profile updated email: %s", e)

    user_data = await build_user_base_response(
        current_user,
        profile,
        db
    )

    return {
        "user": user_data
    }

async def delete_user_me(user: User, db: AsyncSession) -> dict:
    from datetime import timedelta
    from core.auth.services import revoke_firebase_tokens
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select
    user.status = UserStatus.deleting
    user.is_deleted = True
    now = _now()
    user.deleted_at = now
    user.purge_after = now + timedelta(days=1)
    db.add(user)
    await db.commit()
    await db.refresh(user)
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
        "user": user_data
    }

def update_profile(payload: ProfileUpdateRequest) -> dict:
    profile_data = payload.model_dump(exclude_none=True)
    if "profilePhotoUrl" in profile_data:
        profile_data["profilePhotoUrl"] = normalize_image_name(profile_data["profilePhotoUrl"])
    if "bannerPhotoUrl" in profile_data:
        profile_data["bannerPhotoUrl"] = normalize_image_name(profile_data["bannerPhotoUrl"])
    return {"updated": True, "profile": profile_data, "onboarding_status": "completed", "is_onboarding_completed": True}

def update_visibility(payload: ProfileVisibilityRequest) -> dict:
    return {"profileVisibility": payload.profileVisibility}

def _build_user_base(seed: str = "me") -> UserBaseResponse:
    now = _now()
    normalized_username = seed.lower() or "user"
    email_seed = normalized_username.split("@", 1)[0]
    normalized_email = normalized_username if "@" in normalized_username else f"{email_seed}@example.com"
    return UserBaseResponse(
        id=str(uuid4()),
        firstName="",
        lastName="",
        email=normalized_email,
        createdAt=now,
        updatedAt=now,
    )

def get_me(token: str) -> dict:
    seed = token.replace("access_", "").replace("refresh_", "").strip() or "me"
    user = _build_user_base(seed)
    return {"user": user.model_dump()}

async def get_me_completeness(user_id: UUID, db: AsyncSession) -> dict:
    from sqlmodel import select
    from apps.profiles.db_models import Profile

    stmt = select(Profile).where(Profile.user_id == user_id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    score = profile.completeness_score if profile else 0
    return {"completeness_score": score}

async def get_my_profile_service(
    user: User,
    db: AsyncSession,
    target_user_id: UUID | None = None,
) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from fastapi import HTTPException, status
    from sqlmodel import select

    effective_user_id = target_user_id or user.id
    target_user = user
    if effective_user_id != user.id:
        target_user = (
            await db.execute(select(User).where(User.id == effective_user_id))
        ).scalar_one_or_none()
        if not target_user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    stmt = select(Profile).where(Profile.user_id == effective_user_id)
    profile = (await db.execute(stmt)).scalar_one_or_none()

    if not profile and effective_user_id == user.id:
        profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)

    user_data = await build_user_base_response(target_user, profile, db)

    # Inject relationship flags
    if effective_user_id == user.id:
        user_data.update({
            "is_connected": False,
            "request_sent": False,
            "request_send": False,
            "request_received": False,
            "is_sent": False,
            "is_request": False,
        })
    else:
        from apps.connections.services import get_relationship_flags
        flags_map = await get_relationship_flags(db, user.id, [effective_user_id])
        flags = flags_map.get(effective_user_id, {})
        user_data.update({
            "is_connected": flags.get("is_connected", False),
            "request_sent": flags.get("request_sent", False),
            "request_send": flags.get("request_sent", False),
            "request_received": flags.get("request_received", False),
            "is_sent": flags.get("request_sent", False),
            "is_request": flags.get("request_received", False),
        })
    return {"user": user_data}

async def update_my_profile_service(user: User, payload: UpdateProfileRequest, db: AsyncSession) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select
    from core.images import file_exists, normalize_image_name
    from fastapi import HTTPException

    stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    if payload.firstName is not None:
        profile.first_name = payload.firstName
    if payload.lastName is not None:
        profile.last_name = payload.lastName
    if payload.major is not None:
        profile.major = payload.major
    if payload.minor is not None:
        profile.minor = payload.minor
    if payload.bio is not None:
        profile.bio = payload.bio

    if payload.education_level_id is not None:
        from common.enums import EducationLevel
        try:
            education_level = EducationLevel.from_id(payload.education_level_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid education_level_id") from exc
        profile.edu_level = education_level.value

    if payload.academic_interests is not None:
        profile.profile_interests_id = await _resolve_academic_interest_ids(payload.academic_interests, db)

    if "profile_photo_key" in payload.model_fields_set:
        if payload.profile_photo_key:
            if not file_exists(payload.profile_photo_key):
                raise HTTPException(status_code=400, detail="profile_photo_key does not reference an uploaded file")
            profile.profile_photo_url = normalize_image_name(payload.profile_photo_key)
        else:
            profile.profile_photo_url = None

    if "banner_photo_key" in payload.model_fields_set:
        if payload.banner_photo_key:
            if not file_exists(payload.banner_photo_key):
                raise HTTPException(status_code=400, detail="banner_photo_key does not reference an uploaded file")
            profile.banner_photo_url = normalize_image_name(payload.banner_photo_key)
        else:
            profile.banner_photo_url = None

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    try:
        import logging
        local_logger = logging.getLogger(__name__)
        from core.email_service import send_profile_updated_email
        full_name = f"{profile.first_name} {profile.last_name}".strip() or None
        await send_profile_updated_email(user.email, full_name)
        local_logger.info("Profile updated email queued for user ID %s", user.id)
    except Exception as e:
        import logging
        local_logger = logging.getLogger(__name__)
        local_logger.exception("Failed to queue profile updated email: %s", e)

    return await get_my_profile_service(user, db)

async def update_profile_visibility_service(user: User, payload: ProfileVisibilityRequest, db: AsyncSession) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select
    from common.enums import ProfileVisibility

    stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    try:
        profile.profile_visibility = ProfileVisibility(payload.profileVisibility)
    except ValueError as exc:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid profileVisibility value",
        ) from exc

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    return await get_my_profile_service(user, db)


async def update_user_profile_by_admin_service(
    user_id: UUID | str,
    payload: UpdateProfileRequest,
    db: AsyncSession,
) -> dict:
    from uuid import UUID
    from sqlmodel import select
    from fastapi import HTTPException, status
    from apps.accounts.db_models import User
    from apps.profiles.db_models.profile_db_model import Profile
    from core.images import file_exists, normalize_image_name

    # 1. Fetch user
    user_uuid = UUID(str(user_id)) if isinstance(user_id, str) else user_id
    user_stmt = select(User).where(User.id == user_uuid)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # 2. Fetch or create profile
    profile_stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(profile_stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    # 3. Apply updates
    if payload.firstName is not None:
        profile.first_name = payload.firstName
    if payload.lastName is not None:
        profile.last_name = payload.lastName
    if payload.major is not None:
        profile.major = payload.major
    if payload.minor is not None:
        profile.minor = payload.minor
    if payload.bio is not None:
        profile.bio = payload.bio

    if payload.university_id is not None:
        if payload.university_id:
            try:
                profile.university_id = UUID(str(payload.university_id))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid university_id"
                )
        else:
            profile.university_id = None

    if payload.education_level_id is not None:
        from common.enums import EducationLevel
        try:
            education_level = EducationLevel.from_id(payload.education_level_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid education_level_id"
            ) from exc
        profile.edu_level = education_level.value

    if payload.academic_interests is not None:
        profile.profile_interests_id = await _resolve_academic_interest_ids(payload.academic_interests, db)

    if payload.profile_photo_key is not None:
        if payload.profile_photo_key:
            if not file_exists(payload.profile_photo_key):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="profile_photo_key does not reference an uploaded file"
                )
            profile.profile_photo_url = normalize_image_name(payload.profile_photo_key)
        else:
            profile.profile_photo_url = None

    if payload.banner_photo_key is not None:
        if payload.banner_photo_key:
            if not file_exists(payload.banner_photo_key):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="banner_photo_key does not reference an uploaded file"
                )
            profile.banner_photo_url = normalize_image_name(payload.banner_photo_key)
        else:
            profile.banner_photo_url = None

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    try:
        import logging
        local_logger = logging.getLogger(__name__)
        from core.email_service import send_profile_updated_email
        full_name = f"{profile.first_name} {profile.last_name}".strip() or None
        await send_profile_updated_email(user.email, full_name)
        local_logger.info("Profile updated email queued for user ID %s", user.id)
    except Exception as e:
        import logging
        local_logger = logging.getLogger(__name__)
        local_logger.exception("Failed to queue profile updated email: %s", e)

    return await get_my_profile_service(user, db)

