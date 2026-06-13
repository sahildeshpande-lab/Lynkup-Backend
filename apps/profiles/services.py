from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import UploadFile
from core.images import normalize_image_name, upload_image_to_s3, generate_download_url
from apps.accounts.schemas import UserBaseResponse

from .schemas import (
    EducationUpdateRequest,
    ProfileUpdateRequest,
    ProfileVisibilityRequest,
    ReportUserRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User
from common.enums import UserStatus, OnboardingStatus


async def build_user_base_response(user: User, profile: Profile | None, db: AsyncSession) -> dict:
    from apps.profiles.db_models.profile_interest_db_model import ProfileInterest
    from sqlmodel import select

    interests = []
    if profile:
        stmt = select(ProfileInterest.interest_tag).where(ProfileInterest.profile_id == profile.id)
        interests = list((await db.execute(stmt)).scalars().all())

    first_name = ""
    last_name = ""
    display_name = profile.display_name if profile else ""
    if display_name:
        parts = display_name.split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

    profile_visibility = "public"
    if profile and profile.profile_visibility:
        profile_visibility = profile.profile_visibility.value if hasattr(profile.profile_visibility, "value") else str(profile.profile_visibility)

    return {
        "id": str(user.id),
        "firstName": first_name,
        "lastName": last_name,
        "email": user.email,
        "role": user.role,
        "profilePhotoUrl": generate_download_url(profile.profile_photo_url) if (profile and profile.profile_photo_url) else None,
        "bannerPhotoUrl": generate_download_url(profile.banner_photo_url) if (profile and profile.banner_photo_url) else None,
        "status": user.status.value if hasattr(user.status, "value") else str(user.status),
        "university": None,
        "major": profile.major if profile else None,
        "minor": profile.minor if profile else None,
        "county": "",
        "educationLevel": profile.edu_level if profile else None,
        "bio": profile.bio if profile else None,
        "academicInterests": interests,
        "graduationDate": profile.graduation_date.isoformat() if (profile and profile.graduation_date) else None,
        "location": profile.location_text if profile else None,
        "profileVisibility": profile_visibility,
        "completenessScore": profile.completeness_score if profile else 0,
        "notificationPreferences": {
            "email": True,
            "push": True,
            "inApp": True
        },
        "isEmailVerified": user.email_verified_at is not None,
        "email_verified_at": user.email_verified_at.isoformat() if user.email_verified_at else None,
        "referenceCode": "",
        "invitationCode": None,
        "invitationDeepLinkUrl": None,
        "invitationWebUrl": None,
        "onlinePresence": profile.online_presence_visible if profile else False,
        "welcomeMessage": profile.welcome_message if profile else None,
        "postsCount": 0,
        "connectionsCount": 0,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "updatedAt": user.updated_at.isoformat() if user.updated_at else None,
        "is_onboarding": user.onboarding_status != OnboardingStatus.completed if hasattr(user, "onboarding_status") else True,
        "connectedUserIds": [],
        "followingUserIds": [],
        "blockedUserIds": [],
        "reportedUserIds": [],
        "lynkupRequestUserIds": []
    }


async def get_profile_me(user: User, db: AsyncSession) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select

    stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user.id, display_name="", completeness_score=0)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
    user_data = await build_user_base_response(user, profile, db)
    return {"user": user_data}


async def update_profile_me(user: User, payload: ProfileUpdateRequest, db: AsyncSession) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from apps.profiles.db_models.profile_interest_db_model import ProfileInterest
    from sqlmodel import select

    stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user.id, display_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    profile_data = payload.model_dump(exclude_none=True)
    if "bio" in profile_data:
        profile.bio = profile_data["bio"]
    if "major" in profile_data:
        profile.major = profile_data["major"]
    if "minor" in profile_data:
        profile.minor = profile_data["minor"]
    if "graduationDate" in profile_data:
        profile.graduation_date = profile_data["graduationDate"]
    if "welcomeMessage" in profile_data:
        profile.welcome_message = profile_data["welcomeMessage"]
    if "profilePhotoUrl" in profile_data:
        uploaded_url = await upload_image_to_s3(profile_data["profilePhotoUrl"], prefix="profiles")
        profile.profile_photo_url = normalize_image_name(uploaded_url)
        profile_data["profilePhotoUrl"] = profile.profile_photo_url
    if "bannerPhotoUrl" in profile_data:
        uploaded_url = await upload_image_to_s3(profile_data["bannerPhotoUrl"], prefix="banners")
        profile.banner_photo_url = normalize_image_name(uploaded_url)
        profile_data["bannerPhotoUrl"] = profile.banner_photo_url

    if "academicInterests" in profile_data:
        delete_stmt = select(ProfileInterest).where(ProfileInterest.profile_id == profile.id)
        old_interests = (await db.execute(delete_stmt)).scalars().all()
        for interest in old_interests:
            await db.delete(interest)
        await db.flush()

        for tag in profile_data["academicInterests"]:
            new_interest = ProfileInterest(profile_id=profile.id, interest_tag=tag)
            db.add(new_interest)

    user.onboarding_status = OnboardingStatus.completed
    db.add(user)
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    return {"updated": True, "profile": profile_data, "onboarding_status": "completed", "is_onboarding": False}


async def delete_user_me(user: User, db: AsyncSession) -> dict:
    from datetime import timedelta
    from core.auth.services import revoke_firebase_tokens
    user.status = UserStatus.deleting
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
    return {
        "deleted": True, 
        "status": user.status.value if hasattr(user.status, "value") else str(user.status), 
        "deleted_at": user.deleted_at,
        "purge_after": user.purge_after
    }




def update_profile(payload: ProfileUpdateRequest) -> dict:
    profile_data = payload.model_dump(exclude_none=True)
    if "profilePhotoUrl" in profile_data:
        profile_data["profilePhotoUrl"] = normalize_image_name(profile_data["profilePhotoUrl"])
    if "bannerPhotoUrl" in profile_data:
        profile_data["bannerPhotoUrl"] = normalize_image_name(profile_data["bannerPhotoUrl"])
    return {"updated": True, "profile": profile_data, "onboarding_status": "completed", "is_onboarding": False}


async def update_education(user_id: str, payload: EducationUpdateRequest, db: AsyncSession) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select
    from uuid import UUID

    try:
        user_uuid = UUID(str(user_id))
    except ValueError:
        user_uuid = user_id

    stmt = select(Profile).where(Profile.user_id == user_uuid)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user_uuid, display_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    if payload.universityId:
        try:
            profile.university_id = UUID(str(payload.universityId))
        except ValueError:
            pass

    profile.major = payload.major
    profile.minor = payload.minor
    profile.edu_level = payload.educationLevel
    if payload.graduationDate is not None:
        profile.graduation_date = payload.graduationDate

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    return {
        "user_id": str(user_id),
        "education": {
            "universityId": str(profile.university_id) if profile.university_id else None,
            "major": profile.major,
            "minor": profile.minor,
            "educationLevel": profile.edu_level,
            "graduationDate": profile.graduation_date.isoformat() if profile.graduation_date else None
        }
    }


def update_visibility(payload: ProfileVisibilityRequest) -> dict:
    return {"profileVisibility": payload.profileVisibility}


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        referenceCode="",
    )


def get_me(token: str) -> dict:
    seed = token.replace("access_", "").replace("refresh_", "").strip() or "me"
    user = _build_user_base(seed)
    return {"user": user.model_dump()}


def get_me_completeness(token: str) -> dict:
    _ = token
    return {"completeness_score": 33}


def get_public_profile(email: str) -> dict:
    return {"email": email, "publicProfile": True}


def follow_user(user_id: str) -> dict:
    return {"userId": user_id, "followed": True}


def unfollow_user(user_id: str) -> dict:
    return {"userId": user_id, "unfollowed": True}


def block_user(user_id: str) -> dict:
    return {"userId": user_id, "blocked": True}


def unblock_user(user_id: str) -> dict:
    return {"userId": user_id, "unblocked": True}


def report_user(user_id: str, payload: ReportUserRequest) -> dict:
    return {"userId": user_id, "report": payload.model_dump(exclude_none=True)}


def request_lynkup(user_id: str) -> dict:
    return {"userId": user_id, "requestSent": True}


def accept_lynkup(user_id: str) -> dict:
    return {"userId": user_id, "accepted": True}


def remove_lynkup(user_id: str) -> dict:
    return {"userId": user_id, "removed": True}


async def update_profile_me_form(
    current_user: User,
    bio: str | None,
    academic_interests: str | None,
    profile_photo: UploadFile | None,
    banner_photo: UploadFile | None,
    db: AsyncSession
) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from apps.profiles.db_models.profile_interest_db_model import ProfileInterest
    from sqlmodel import select
    from core.images import s3_client, settings, generate_download_url, normalize_image_name
    import uuid
    import json

    stmt = select(Profile).where(Profile.user_id == current_user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=current_user.id, display_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    profile_data = {}

    if bio is not None:
        profile.bio = bio
        profile_data["bio"] = bio

    if profile_photo is not None and profile_photo.filename:
        content = await profile_photo.read()
        if content:
            ext = profile_photo.filename.split(".")[-1] if "." in profile_photo.filename else "png"
            file_name = f"profiles/{uuid.uuid4()}.{ext}"
            s3_client.put_object(
                Bucket=settings.aws_s3_bucket,
                Key=file_name,
                Body=content,
                ContentType=profile_photo.content_type or "image/png"
            )
            profile.profile_photo_url = normalize_image_name(file_name)
            profile_data["profilePhotoUrl"] = generate_download_url(profile.profile_photo_url)

    if banner_photo is not None and banner_photo.filename:
        content = await banner_photo.read()
        if content:
            ext = banner_photo.filename.split(".")[-1] if "." in banner_photo.filename else "png"
            file_name = f"banners/{uuid.uuid4()}.{ext}"
            s3_client.put_object(
                Bucket=settings.aws_s3_bucket,
                Key=file_name,
                Body=content,
                ContentType=banner_photo.content_type or "image/png"
            )
            profile.banner_photo_url = normalize_image_name(file_name)
            profile_data["bannerPhotoUrl"] = generate_download_url(profile.banner_photo_url)

    if academic_interests is not None:
        interests_list = []
        val = academic_interests.strip()
        if val.startswith("[") and val.endswith("]"):
            try:
                interests_list = json.loads(val)
            except Exception:
                interests_list = [x.strip() for x in val[1:-1].split(",") if x.strip()]
        else:
            interests_list = [x.strip() for x in val.split(",") if x.strip()]

        delete_stmt = select(ProfileInterest).where(ProfileInterest.profile_id == profile.id)
        old_interests = (await db.execute(delete_stmt)).scalars().all()
        for interest in old_interests:
            await db.delete(interest)
        await db.flush()

        for tag in interests_list:
            new_interest = ProfileInterest(profile_id=profile.id, interest_tag=tag)
            db.add(new_interest)
        
        profile_data["academicInterests"] = interests_list

    current_user.onboarding_status = OnboardingStatus.completed
    db.add(current_user)
    db.add(profile)
    await db.commit()
    await db.refresh(current_user)
    await db.refresh(profile)

    return {"updated": True, "profile": profile_data, "onboarding_status": "completed", "is_onboarding": False}

