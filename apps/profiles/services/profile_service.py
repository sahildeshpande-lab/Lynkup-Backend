from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID, uuid4
from core.images import normalize_image_name
from apps.accounts.schemas import UserBaseResponse
from ..schemas import ProfileUpdateRequest, ProfileVisibilityRequest, UpdateProfileMeRequest, UpdateProfileRequest
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User
from common.enums import UserStatus, OnboardingStatus, EducationLevel

from apps.profiles.normalization import normalize_major_minor

from .completeness_service import calculate_completeness_score
from .interest_service import _resolve_academic_interest_ids
from .response_service import build_user_base_response

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _apply_country_id_update(
    profile,
    country_id: str | None,
    db: AsyncSession,
) -> None:
    from fastapi import HTTPException, status
    from sqlmodel import select
    from apps.profiles.db_models.country_db_model import Country

    if country_id is None:
        return

    if country_id:
        try:
            country_uuid = UUID(str(country_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid country_id",
            ) from exc

        country = (
            await db.execute(select(Country).where(Country.id == country_uuid))
        ).scalar_one_or_none()
        if not country:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid country_id",
            )
        profile.country_id = country_uuid
    else:
        profile.country_id = None


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
        profile.major = normalize_major_minor(payload.major)

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

    from apps.recommendations.services.post_keyword_service import (
        refresh_profile_extracted_keywords_best_effort,
    )
    await refresh_profile_extracted_keywords_best_effort(db, user_id=current_user.id)

    

    # Temporarily disabled: profile updated email
    # try:
    #     import logging
    #     local_logger = logging.getLogger(__name__)
    #     from core.email_service import send_profile_updated_email
    #     full_name = f"{profile.first_name} {profile.last_name}".strip() or None
    #     await send_profile_updated_email(current_user.email, full_name)
    #     local_logger.info("Profile updated email queued for user ID %s", current_user.id)
    # except Exception as e:
    #     import logging
    #     local_logger = logging.getLogger(__name__)
    #     local_logger.exception("Failed to queue profile updated email: %s", e)

    user_data = await build_user_base_response(
        current_user,
        profile,
        db
    )

    return {
        "user": user_data
    }

async def delete_user_me(user: User, db: AsyncSession) -> dict:
    import logging
    from datetime import timedelta
    from core.auth.services import disable_firebase_user, revoke_firebase_tokens
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select

    logger = logging.getLogger(__name__)

    # Idempotent: already scheduled for deletion
    if user.is_deleted or user.status == UserStatus.deleting or user.deleted_at is not None:
        profile = (
            await db.execute(select(Profile).where(Profile.user_id == user.id))
        ).scalar_one_or_none()
        user_data = await build_user_base_response(user, profile, db)
        return {
            "deleted": True,
            "status": user.status.value if hasattr(user.status, "value") else str(user.status),
            "deleted_at": user.deleted_at,
            "purge_after": user.purge_after,
            "user": user_data,
        }

    user.status = UserStatus.deleting
    user.is_deleted = True
    now = _now()
    user.deleted_at = now
    user.purge_after = now + timedelta(days=1)
    db.add(user)

    from apps.profiles.services.profile_stats_service import (
        adjust_counts_for_deleting_user,
    )

    await adjust_counts_for_deleting_user(db, user.id)
    await db.commit()
    await db.refresh(user)

    if user.firebase_uid and not str(user.firebase_uid).startswith("admin-"):
        try:
            disable_firebase_user(user.firebase_uid)
        except Exception:
            logger.exception(
                "Failed to disable Firebase user during self-deletion uid=%s",
                user.firebase_uid,
            )
            try:
                revoke_firebase_tokens(user.firebase_uid)
            except Exception:
                logger.exception(
                    "Failed to revoke Firebase tokens during self-deletion uid=%s",
                    user.firebase_uid,
                )

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user.id))
    ).scalar_one_or_none()
    user_data = await build_user_base_response(user, profile, db)
    return {
        "deleted": True,
        "status": user.status.value if hasattr(user.status, "value") else str(user.status),
        "deleted_at": user.deleted_at,
        "purge_after": user.purge_after,
        "user": user_data,
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

    # Inject relationship flags when viewing another user's profile
    if effective_user_id != user.id:
        from apps.connections.services.connection_service import get_relationship_flags
        flags_map = await get_relationship_flags(db, user.id, [effective_user_id])
        flags = flags_map.get(effective_user_id, {})
        user_data["is_connected"] = flags.get("is_connected", False)
        user_data["request_sent"] = flags.get("request_sent", False)
        user_data["request_received"] = flags.get("request_received", False)
        user_data["is_sent"] = flags.get("request_sent", False)
        user_data["is_request"] = flags.get("request_received", False)
    else:
        # Own profile — these flags are always False / N/A
        user_data["is_connected"] = False
        user_data["request_sent"] = False
        user_data["request_received"] = False
        user_data["is_sent"] = False
        user_data["is_request"] = False

    return {"user": user_data}

async def update_my_profile_service(user: User, payload: UpdateProfileRequest, db: AsyncSession) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select
    from core.images import file_exists, normalize_image_name
    from fastapi import HTTPException
    import logging

    from apps.notifications.services.topic_service import TopicService

    profile_logger = logging.getLogger(__name__)

    stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    topic_fields_changed = TopicService.affects_topics(payload)
    old_topics: set[str] = set()
    if topic_fields_changed:
        old_topics = await TopicService.capture_topics(db, profile)

    if payload.firstName is not None:
        profile.first_name = payload.firstName
    if payload.lastName is not None:
        profile.last_name = payload.lastName
    stream_sync_needed = (
        payload.firstName is not None
        or payload.lastName is not None
        or "profile_photo_key" in payload.model_fields_set
    )
    if payload.major is not None:
        profile.major = normalize_major_minor(payload.major)
    if payload.minor is not None:
        profile.minor = normalize_major_minor(payload.minor)
    if payload.bio is not None:
        profile.bio = payload.bio

    if payload.university_id is not None:
        if payload.university_id:
            try:
                from uuid import UUID as _UUID
                profile.university_id = _UUID(str(payload.university_id))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid university_id")
        else:
            profile.university_id = None

    await _apply_country_id_update(profile, payload.country_id, db)

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

    # Recalculate completeness score before saving so the updated fields
    # are reflected immediately in the response.
    profile.completeness_score = await calculate_completeness_score(user.id, db)

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    from apps.recommendations.services.post_keyword_service import (
        refresh_profile_extracted_keywords_best_effort,
    )
    await refresh_profile_extracted_keywords_best_effort(db, user_id=user.id)

    if stream_sync_needed:
        from apps.chat.service import sync_stream_user_on_auth

        # Best-effort: missing Stream credentials must not fail profile updates.
        await sync_stream_user_on_auth(user, db)

    if topic_fields_changed:
        await TopicService.sync_user_topics(
            db,
            user.id,
            old_topics=old_topics,
            profile=profile,
        )

    # Temporarily disabled: profile updated email
    # try:
    #     import logging
    #     local_logger = logging.getLogger(__name__)
    #     from core.email_service import send_profile_updated_email
    #     full_name = f"{profile.first_name} {profile.last_name}".strip() or None
    #     await send_profile_updated_email(user.email, full_name)
    #     local_logger.info("Profile updated email queued for user ID %s", user.id)
    # except Exception as e:
    #     import logging
    #     local_logger = logging.getLogger(__name__)
    #     local_logger.exception("Failed to queue profile updated email: %s", e)

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
    from apps.notifications.services.topic_service import TopicService

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

    topic_fields_changed = TopicService.affects_topics(payload)
    old_topics: set[str] = set()
    if topic_fields_changed:
        old_topics = await TopicService.capture_topics(db, profile)

    # 3. Apply updates
    if payload.firstName is not None:
        profile.first_name = payload.firstName
    if payload.lastName is not None:
        profile.last_name = payload.lastName
    stream_sync_needed = (
        payload.firstName is not None
        or payload.lastName is not None
        or payload.profile_photo_key is not None
    )
    if payload.major is not None:
        profile.major = normalize_major_minor(payload.major)
    if payload.minor is not None:
        profile.minor = normalize_major_minor(payload.minor)
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

    await _apply_country_id_update(profile, payload.country_id, db)

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

    # Recalculate completeness score before saving so the updated fields
    # are reflected immediately in the response.
    profile.completeness_score = await calculate_completeness_score(user.id, db)

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    from apps.recommendations.services.post_keyword_service import (
        refresh_profile_extracted_keywords_best_effort,
    )
    await refresh_profile_extracted_keywords_best_effort(db, user_id=user.id)
    if stream_sync_needed:
        from apps.chat.service import sync_stream_user_on_auth

        # Best-effort: missing Stream credentials must not fail profile updates.
        await sync_stream_user_on_auth(user, db)

    if topic_fields_changed:
        await TopicService.sync_user_topics(
            db,
            user.id,
            old_topics=old_topics,
            profile=profile,
        )

    # Temporarily disabled: profile updated email
    # try:
    #     import logging
    #     local_logger = logging.getLogger(__name__)
    #     from core.email_service import send_profile_updated_email
    #     full_name = f"{profile.first_name} {profile.last_name}".strip() or None
    #     await send_profile_updated_email(user.email, full_name)
    #     local_logger.info("Profile updated email queued for user ID %s", user.id)
    # except Exception as e:
    #     import logging
    #     local_logger = logging.getLogger(__name__)
    #     local_logger.exception("Failed to queue profile updated email: %s", e)

    return await get_my_profile_service(user, db)

