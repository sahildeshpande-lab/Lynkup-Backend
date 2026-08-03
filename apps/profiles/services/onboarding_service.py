from __future__ import annotations
import logging
from fastapi import HTTPException, UploadFile, status
from core.images import normalize_image_name, generate_profile_image_url
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User
from common.enums import OnboardingStatus, EducationLevel, UserStatus

from apps.profiles.normalization import normalize_major_minor

from .completeness_service import calculate_completeness_score
from .interest_service import _resolve_academic_interest_ids
from .response_service import build_user_base_response

logger = logging.getLogger(__name__)

async def complete_onboarding(
    user: User,
    bio: str | None ,
    major: str,
    minor: str | None,
    country_id: str,
    university_id: str,
    education_level_id: int,
    academic_interests: list[str],
    profile_photo_key: str | None ,
    banner_photo_key :str | None ,
    db: AsyncSession,
    invitation_code: str | None = None,
) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select
    from core.images import generate_profile_image_url, normalize_image_name, file_exists
    from uuid import UUID
    from common.enums import EducationLevel, OnboardingStatus

    stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()

    if not profile:
        profile = Profile(
            user_id=user.id,
            first_name="",
            last_name="",
            completeness_score=0,
        )
    db.add(profile)
    await db.flush()

    from apps.profiles.services.profile_stats_service import get_or_create_profile_stats
    await get_or_create_profile_stats(db, profile.id)

    profile_data = {}

    # Validate that the uploaded image key exists in storage
    if profile_photo_key:
        if not file_exists(profile_photo_key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="profile_photo_key does not reference an uploaded file"
            )

        profile.profile_photo_url = normalize_image_name(profile_photo_key)
        profile_data["profilePhotoUrl"] = generate_profile_image_url(
        profile.profile_photo_url
        )
    else:
        profile_data["profilePhotoUrl"] = (
        generate_profile_image_url(profile.profile_photo_url)
        if profile.profile_photo_url
        else None
    )
    # Optional banner photo
    if banner_photo_key:
        if not file_exists(banner_photo_key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="banner_photo_key does not reference an uploaded file"
            )

        profile.banner_photo_url = normalize_image_name(
            banner_photo_key
    )

    else :
        profile_data["bannerPhotoUrl"] = (
        generate_profile_image_url(profile.banner_photo_url)
        if profile.banner_photo_url
        else None
    )

    if bio is not None :
        profile.bio = bio
    profile_data["bio"] = profile.bio

    if not profile:
        profile = Profile(user_id=user.id, first_name="", last_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    if country_id:
        try:
            country_uuid = UUID(str(country_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid country_id",
            ) from exc
        from apps.profiles.db_models.country_db_model import Country
        country = (await db.execute(select(Country).where(Country.id == country_uuid))).scalar_one_or_none()
        if not country:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid country_id",
            )
        profile.country_id = country_uuid
    profile_data["countryId"] = str(profile.country_id) if profile.country_id else None

    if university_id:
        try:
            profile.university_id = UUID(str(university_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid university_id",
            ) from exc
    profile_data["universityId"] = str(profile.university_id) if profile.university_id else None

    major = normalize_major_minor(major)
    minor = normalize_major_minor(minor)

    profile.major = major
    profile_data["major"] = major

    profile.minor = minor
    profile_data["minor"] = minor

    # profile.profile_photo_url = normalize_image_name(profile_photo_key)
    # profile_data["profilePhotoUrl"] = generate_download_url(profile.profile_photo_url)

    try:
        education_level = EducationLevel.from_id(education_level_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid education_level_id"
        ) from exc

    profile.edu_level = education_level.value
    profile_data["educationLevel"] = education_level.value

    if academic_interests is not None:
        profile.profile_interests_id = await _resolve_academic_interest_ids(academic_interests, db)
        profile_data["academicInterests"] = academic_interests

    user.onboarding_status = OnboardingStatus.completed
    # Completing onboarding implies a verified account; keep or promote to active.
    if user.status != UserStatus.active:
        user.status = UserStatus.active
    db.add(user)
    db.add(profile)
    await db.flush()

    if invitation_code:
        from apps.invitations.services import redeem_invitation

        invitation = await redeem_invitation(
            db,
            code=invitation_code,
            redeemed_by_user_id=user.id,
            commit=False,
        )
        user.referred_by_user_id = invitation.inviter_user_id
        db.add(user)
        await db.flush()

    profile.completeness_score = await calculate_completeness_score(user.id, db)
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    from apps.recommendations.services.post_keyword_service import (
        refresh_profile_extracted_keywords_best_effort,
    )
    await refresh_profile_extracted_keywords_best_effort(db, user_id=user.id)

    from apps.chat.service import sync_stream_user_on_auth
    from apps.notifications.services.topic_service import TopicService

    # Best-effort Stream sync: missing Stream credentials must not fail onboarding.
    await sync_stream_user_on_auth(user, db)

    await TopicService.refresh_user_topic_subscriptions(db, user.id, profile)

    user_data = await build_user_base_response(user, profile, db)
    return {"user": user_data}


async def update_profile_me_form(
    current_user: User,
    bio: str | None,
    academic_interests: str | None,
    profile_photo: UploadFile | None,
    banner_photo: UploadFile | None,
    db: AsyncSession
) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from apps.notifications.services.topic_service import TopicService
    from sqlmodel import select
    from core.images import save_image, settings, generate_profile_image_url, normalize_image_name
    import uuid
    import json

    stmt = select(Profile).where(Profile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=current_user.id, first_name="", last_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    topic_sync_needed = False
    old_topics: set[str] = set()

    profile_data = {}

    if bio is not None:
        profile.bio = bio
        profile_data["bio"] = bio

    if profile_photo is not None and profile_photo.filename:
        content = await profile_photo.read()
        if content:
            ext = profile_photo.filename.split(".")[-1] if "." in profile_photo.filename else "png"
            file_name = f"profiles/{uuid.uuid4()}.{ext}"
            save_image(
                file_name=file_name,
                content=content,
                content_type=profile_photo.content_type or "image/png"
            )
            profile.profile_photo_url = normalize_image_name(file_name)
            profile_data["profilePhotoUrl"] = generate_profile_image_url(profile.profile_photo_url)

    if banner_photo is not None and banner_photo.filename:
        content = await banner_photo.read()
        if content:
            ext = banner_photo.filename.split(".")[-1] if "." in banner_photo.filename else "png"
            file_name = f"banners/{uuid.uuid4()}.{ext}"
            save_image(
                file_name=file_name,
                content=content,
                content_type=banner_photo.content_type or "image/png"
            )
            profile.banner_photo_url = normalize_image_name(file_name)
            profile_data["bannerPhotoUrl"] = generate_profile_image_url(profile.banner_photo_url)

    if academic_interests is not None:
        topic_sync_needed = True
        old_topics = await TopicService.capture_topics(db, profile)
        interests_list = []
        val = academic_interests.strip()
        if val.startswith("[") and val.endswith("]"):
            try:
                interests_list = json.loads(val)
            except Exception:
                interests_list = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        else:
            interests_list = [x.strip() for x in val.split(",") if x.strip()]

        profile.profile_interests_id = await _resolve_academic_interest_ids(interests_list, db)
        profile_data["academicInterests"] = interests_list

    current_user.onboarding_status = OnboardingStatus.completed
    if current_user.status != UserStatus.active:
        current_user.status = UserStatus.active
    db.add(current_user)
    db.add(profile)
    await db.flush()
    profile.completeness_score = await calculate_completeness_score(current_user.id, db)
    db.add(profile)
    await db.commit()
    await db.refresh(current_user)
    await db.refresh(profile)

    from apps.recommendations.services.post_keyword_service import (
        refresh_profile_extracted_keywords_best_effort,
    )
    await refresh_profile_extracted_keywords_best_effort(db, user_id=current_user.id)

    if topic_sync_needed:
        await TopicService.sync_user_topics(
            db,
            current_user.id,
            old_topics=old_topics,
            profile=profile,
        )

    user_data = await build_user_base_response(current_user, profile, db)
    return {"user": user_data}
