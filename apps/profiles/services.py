from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import UploadFile
from core.images import normalize_image_name, upload_image_to_s3, generate_download_url
from apps.accounts.schemas import UserBaseResponse

from .schemas import (
    ProfileUpdateRequest,
    ProfileVisibilityRequest,
    ReportUserRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User
from common.enums import UserStatus, OnboardingStatus


async def build_user_base_response(
    user: User,
    profile: Profile | None,
    db: AsyncSession,
    *,
    university_name: str | None = None,
    country_name: str | None = None,
    interests: list[str] | None = None,
) -> dict:
    from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
    from sqlmodel import select
    from uuid import UUID

    if interests is None:
        interests = []
        if profile and profile.profile_interests_id:
            try:
                uuid_list = [UUID(str(u)) for u in profile.profile_interests_id if u]
                if uuid_list:
                    stmt = select(AcademicInterest.name).where(AcademicInterest.id.in_(uuid_list))
                    interests = list((await db.execute(stmt)).scalars().all())
            except Exception:
                pass

    if university_name is None and profile and profile.university_id:
        from apps.profiles.db_models.university_db_model import University
        try:
            stmt = select(University.name).where(University.id == profile.university_id)
            university_name = (await db.execute(stmt)).scalar_one_or_none()
        except Exception:
            pass

    if country_name is None and profile and profile.country_id:
        from apps.profiles.db_models.country_db_model import Country
        try:
            stmt = select(Country.name).where(Country.id == profile.country_id)
            country_name = (await db.execute(stmt)).scalar_one_or_none()
        except Exception:
            pass

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
        "firebase_uid": user.firebase_uid,
        "loginType": user.registration_type.value if hasattr(user.registration_type, "value") else str(user.registration_type),
        "profilePhoto_url": generate_download_url(profile.profile_photo_url) if (profile and profile.profile_photo_url) else None,
        "bannerPhotoUrl": generate_download_url(profile.banner_photo_url) if (profile and profile.banner_photo_url) else None,
        "status": user.status.value if hasattr(user.status, "value") else str(user.status),
        "university": university_name if university_name is not None else (str(profile.university_id) if (profile and profile.university_id) else None),
        "major": profile.major if profile else None,
        "minor": profile.minor if profile else None,
        "country": country_name,
        "county": country_name or "",
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
        "is_deleted": user.is_deleted,
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

    if "academicInterests" in profile_data and profile_data["academicInterests"] is not None:
        from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
        from uuid import UUID

        resolved_uuids = []
        for tag in profile_data["academicInterests"]:
            tag_clean = tag.strip()
            if not tag_clean:
                continue
            is_uuid = False
            try:
                uuid_val = UUID(tag_clean)
                is_uuid = True
            except ValueError:
                pass
            
            if is_uuid:
                stmt_interest = select(AcademicInterest).where(AcademicInterest.id == uuid_val)
                interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
                if interest_rec:
                    resolved_uuids.append(str(interest_rec.id))
            else:
                stmt_interest = select(AcademicInterest).where(AcademicInterest.name.ilike(tag_clean))
                interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
                if not interest_rec:
                    interest_rec = AcademicInterest(name=tag_clean, is_active=True)
                    db.add(interest_rec)
                    await db.flush()
                resolved_uuids.append(str(interest_rec.id))

        profile.profile_interests_id = resolved_uuids

    db.add(user)
    db.add(profile)
    await db.flush()
    profile.completeness_score = await calculate_completeness_score(user.id, db)
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    user_data = await build_user_base_response(user, profile, db)
    return {"user": user_data}


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
    return {"updated": True, "profile": profile_data, "onboarding_status": "completed", "is_onboarding": False}


async def complete_onboarding(
    user: User,
    bio: str,
    major: str,
    minor: str | None,
    university_id: str,
    education_level: str,
    academic_interests: str,
    profile_photo: UploadFile,
    db: AsyncSession,
) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select
    from core.images import save_image, settings, generate_download_url, normalize_image_name
    from uuid import UUID
    import uuid
    import json
    from common.enums import OnboardingStatus

    stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    if not profile:
        profile = Profile(user_id=user.id, display_name="", completeness_score=0)
        db.add(profile)
        await db.flush()

    profile_data = {}

    if profile_photo and profile_photo.filename:
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
            profile_data["profilePhotoUrl"] = generate_download_url(profile.profile_photo_url)

    profile.bio = bio
    profile_data["bio"] = bio

    if university_id:
        try:
            profile.university_id = UUID(str(university_id))
        except ValueError:
            pass
    profile_data["universityId"] = str(profile.university_id) if profile.university_id else None

    profile.major = major
    profile_data["major"] = major

    profile.minor = minor
    profile_data["minor"] = minor

    profile.edu_level = education_level
    profile_data["educationLevel"] = education_level

    if academic_interests is not None:
        interests_list = []
        val = academic_interests.strip()
        if val.startswith("[") and val.endswith("]"):
            try:
                interests_list = json.loads(val)
            except Exception:
                interests_list = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        else:
            interests_list = [x.strip() for x in val.split(",") if x.strip()]

        from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
        from uuid import UUID

        resolved_uuids = []
        for tag in interests_list:
            tag_clean = tag.strip()
            if not tag_clean:
                continue
            is_uuid = False
            try:
                uuid_val = UUID(tag_clean)
                is_uuid = True
            except ValueError:
                pass
            
            if is_uuid:
                stmt_interest = select(AcademicInterest).where(AcademicInterest.id == uuid_val)
                interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
                if interest_rec:
                    resolved_uuids.append(str(interest_rec.id))
            else:
                stmt_interest = select(AcademicInterest).where(AcademicInterest.name.ilike(tag_clean))
                interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
                if not interest_rec:
                    interest_rec = AcademicInterest(name=tag_clean, is_active=True)
                    db.add(interest_rec)
                    await db.flush()
                resolved_uuids.append(str(interest_rec.id))

        profile.profile_interests_id = resolved_uuids
        profile_data["academicInterests"] = interests_list

    user.onboarding_status = OnboardingStatus.completed
    db.add(user)
    db.add(profile)
    await db.flush()
    profile.completeness_score = await calculate_completeness_score(user.id, db)
    db.add(profile)
    await db.commit()
    await db.refresh(user)
    await db.refresh(profile)

    user_data = await build_user_base_response(user, profile, db)
    return {"user": user_data}



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


async def get_me_completeness(token: str, db: AsyncSession) -> dict:
    import jwt
    from core.auth.config import settings as auth_settings
    from sqlmodel import select
    from apps.profiles.db_models import Profile
    from uuid import UUID
    from fastapi import HTTPException, status

    try:
        decoded = jwt.decode(token, auth_settings.jwt_secret, algorithms=[auth_settings.jwt_algorithm])
        user_id = decoded.get("sub")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from exc

    stmt = select(Profile).where(Profile.user_id == UUID(user_id))
    profile = (await db.execute(stmt)).scalar_one_or_none()
    score = profile.completeness_score if profile else 0
    return {"completeness_score": score}


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
    from sqlmodel import select
    from core.images import save_image, settings, generate_download_url, normalize_image_name
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
            save_image(
                file_name=file_name,
                content=content,
                content_type=profile_photo.content_type or "image/png"
            )
            profile.profile_photo_url = normalize_image_name(file_name)
            profile_data["profilePhotoUrl"] = generate_download_url(profile.profile_photo_url)

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
            profile_data["bannerPhotoUrl"] = generate_download_url(profile.banner_photo_url)

    if academic_interests is not None:
        interests_list = []
        val = academic_interests.strip()
        if val.startswith("[") and val.endswith("]"):
            try:
                interests_list = json.loads(val)
            except Exception:
                interests_list = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        else:
            interests_list = [x.strip() for x in val.split(",") if x.strip()]

        from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
        from uuid import UUID

        resolved_uuids = []
        for tag in interests_list:
            tag_clean = tag.strip()
            if not tag_clean:
                continue
            is_uuid = False
            try:
                uuid_val = UUID(tag_clean)
                is_uuid = True
            except ValueError:
                pass
            
            if is_uuid:
                stmt_interest = select(AcademicInterest).where(AcademicInterest.id == uuid_val)
                interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
                if interest_rec:
                    resolved_uuids.append(str(interest_rec.id))
            else:
                stmt_interest = select(AcademicInterest).where(AcademicInterest.name.ilike(tag_clean))
                interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
                if not interest_rec:
                    interest_rec = AcademicInterest(name=tag_clean, is_active=True)
                    db.add(interest_rec)
                    await db.flush()
                resolved_uuids.append(str(interest_rec.id))

        profile.profile_interests_id = resolved_uuids
        profile_data["academicInterests"] = interests_list

    current_user.onboarding_status = OnboardingStatus.completed
    db.add(current_user)
    db.add(profile)
    await db.flush()
    profile.completeness_score = await calculate_completeness_score(current_user.id, db)
    db.add(profile)
    await db.commit()
    await db.refresh(current_user)
    await db.refresh(profile)

    user_data = await build_user_base_response(current_user, profile, db)
    return {"user": user_data}


async def get_completeness_weights(db: AsyncSession) -> CompletenessWeight:
    from apps.profiles.db_models import CompletenessWeight
    from sqlmodel import select

    stmt = select(CompletenessWeight).where(CompletenessWeight.id == 1)
    weights = (await db.execute(stmt)).scalar_one_or_none()
    if not weights:
        weights = CompletenessWeight()
        db.add(weights)
        await db.commit()
        await db.refresh(weights)
    return weights


async def calculate_completeness_score(user_id, db: AsyncSession) -> int:
    from apps.accounts.db_models import User
    from apps.profiles.db_models import Profile
    from sqlmodel import select
    from uuid import UUID

    user_uuid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id

    stmt_user = select(User).where(User.id == user_uuid)
    user = (await db.execute(stmt_user)).scalar_one_or_none()
    if not user:
        return 0

    stmt_profile = select(Profile).where(Profile.user_id == user_uuid)
    profile = (await db.execute(stmt_profile)).scalar_one_or_none()
    if not profile:
        return 0

    weights = await get_completeness_weights(db)
    filled_fields = []

    if profile.bio and profile.bio.strip():
        filled_fields.append(("bio", weights.bio))

    if profile.university_id:
        filled_fields.append(("university", weights.university))

    if profile.major and profile.major.strip():
        filled_fields.append(("major", weights.major))

    if profile.edu_level and profile.edu_level.strip():
        filled_fields.append(("edu_level", weights.edu_level))

    display_name = profile.display_name or ""
    parts = display_name.split(" ", 1) if display_name else []
    if len(parts) > 0 and parts[0].strip():
        filled_fields.append(("first_name", weights.first_name))
    if len(parts) > 1 and parts[1].strip():
        filled_fields.append(("last_name", weights.last_name))

    if user.email and user.email.strip():
        filled_fields.append(("email", weights.email))

    if profile.profile_photo_url and profile.profile_photo_url.strip():
        filled_fields.append(("profile_photo_url", weights.profile_photo_url))

    if profile.profile_interests_id and len(profile.profile_interests_id) > 0:
        filled_fields.append(("interests", weights.interests))

    if profile.graduation_date:
        filled_fields.append(("graduation_date", weights.graduation_date))

    if profile.location_text and profile.location_text.strip():
        filled_fields.append(("location", weights.location))

    sum_of_weights = sum(item[1] for item in filled_fields)
    total_weights_sum = (
        weights.bio + weights.university + weights.major + weights.edu_level +
        weights.first_name + weights.last_name + weights.email +
        weights.profile_photo_url + weights.interests + weights.graduation_date +
        weights.location
    )
    if total_weights_sum == 0:
        return 0
    calculated_score = (sum_of_weights * 100) / total_weights_sum
    return min(int(round(calculated_score)), 100)


async def update_completeness_weights(payload, db: AsyncSession) -> dict:
    from apps.profiles.db_models import Profile
    from sqlmodel import select

    weights = await get_completeness_weights(db)
    update_data = payload.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        if val is not None:
            setattr(weights, field, val)
    db.add(weights)
    await db.commit()
    await db.refresh(weights)

    # Recalculate completeness score for all profiles
    stmt = select(Profile)
    profiles = (await db.execute(stmt)).scalars().all()
    for profile in profiles:
        profile.completeness_score = await calculate_completeness_score(profile.user_id, db)
        db.add(profile)
    await db.commit()

    return {
        "message": "Completeness weights updated and all profiles recalculated.",
        "weights": {
            k: getattr(weights, k)
            for k in ["bio", "university", "major", "edu_level", "first_name", "last_name", "email", "profile_photo_url", "interests", "graduation_date", "location"]
        }
    }

