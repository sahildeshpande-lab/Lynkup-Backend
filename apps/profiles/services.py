from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


from core.images import normalize_image_name, upload_image_to_s3, generate_download_url
from apps.accounts.schemas import UserBaseResponse

from .schemas import (
    ProfileUpdateRequest,
    ProfileVisibilityRequest,
    ReportUserRequest,
    UpdateProfileMeRequest,
    UpdateProfileRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User
from common.enums import UserStatus, OnboardingStatus


def _normalize_name_part(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def _compose_full_name(first_name: str | None, last_name: str | None) -> str:
    parts = [_normalize_name_part(first_name), _normalize_name_part(last_name)]
    return " ".join(part for part in parts if part).strip()


async def _resolve_academic_interest_ids(values: list[str | int], db: AsyncSession) -> list[int]:
    from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
    from sqlmodel import select

    resolved_ids: list[int] = []
    for value in values:
        if value is None:
            continue
        tag_clean = str(value).strip()
        if not tag_clean:
            continue

        if tag_clean.isdigit():
            interest_id = int(tag_clean)
            stmt_interest = select(AcademicInterest).where(AcademicInterest.id == interest_id)
            interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
            if interest_rec:
                resolved_ids.append(interest_id)
            continue

        stmt_interest = select(AcademicInterest).where(AcademicInterest.name.ilike(tag_clean))
        interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
        if not interest_rec:
            interest_rec = AcademicInterest(name=tag_clean, is_active=True)
            db.add(interest_rec)
            await db.flush()
        if interest_rec.id is not None:
            resolved_ids.append(int(interest_rec.id))

    return resolved_ids


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

    if interests is None:
        interests = []
        if profile and profile.profile_interests_id:
            try:
                id_list = [int(u) for u in profile.profile_interests_id if u is not None]
                if id_list:
                    stmt = select(AcademicInterest.name).where(AcademicInterest.id.in_(id_list))
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

    first_name = profile.first_name if profile and profile.first_name else ""
    last_name = profile.last_name if profile and profile.last_name else ""

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
        "is_onboarding_completed": user.onboarding_status == OnboardingStatus.completed if hasattr(user, "onboarding_status") else False,
        "is_deleted": user.is_deleted
    }


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


async def complete_onboarding(
    user: User,
    bio: str | None ,
    major: str,
    minor: str | None,
    university_id: str,
    education_level_id: int,
    academic_interests: list[str],
    profile_photo_key: str | None ,
    banner_photo_key :str | None ,
    db: AsyncSession,
) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select
    from core.images import generate_download_url, normalize_image_name, file_exists
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

    profile_data = {}

    # Validate that the uploaded image key exists in storage
    if profile_photo_key:
        if not file_exists(profile_photo_key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="profile_photo_key does not reference an uploaded file"
            )

        profile.profile_photo_url = normalize_image_name(profile_photo_key)
        profile_data["profilePhotoUrl"] = generate_download_url(
        profile.profile_photo_url
    )
    else:
        profile_data["profilePhotoUrl"] = (
        generate_download_url(profile.profile_photo_url)
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
        generate_download_url(profile.banner_photo_url)
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



    # profile.profile_photo_url = normalize_image_name(profile_photo_key)
    # profile_data["profilePhotoUrl"] = generate_download_url(profile.profile_photo_url)



    try:
        education_level = EducationLevel.from_id(education_level_id)
    except (TypeError, ValueError) as exc:
        from fastapi import HTTPException, status
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


async def get_me_completeness(user_id: UUID, db: AsyncSession) -> dict:
    from sqlmodel import select
    from apps.profiles.db_models import Profile

    stmt = select(Profile).where(Profile.user_id == user_id)
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
        profile = Profile(user_id=current_user.id, first_name="", last_name="", completeness_score=0)
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

        profile.profile_interests_id = await _resolve_academic_interest_ids(interests_list, db)
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

    if profile.first_name and profile.first_name.strip():
        filled_fields.append(("first_name", weights.first_name))
    if profile.last_name and profile.last_name.strip():
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


async def get_my_profile_service(user: User, db: AsyncSession) -> dict:
    from apps.profiles.db_models.profile_db_model import Profile
    from sqlmodel import select
    from core.images import generate_download_url

    stmt = select(Profile).where(Profile.user_id == user.id)
    profile = (await db.execute(stmt)).scalar_one_or_none()
    
    first_name = profile.first_name if profile else ""
    last_name = profile.last_name if profile else ""
    major = profile.major if profile else ""
    minor = profile.minor if profile else ""
    bio = profile.bio if profile else ""
    profile_photo_key = profile.profile_photo_url if profile else ""
    banner_photo_key = profile.banner_photo_url if profile else ""
    
    profile_visibility = "public"
    if profile and profile.profile_visibility:
        profile_visibility = profile.profile_visibility.value if hasattr(profile.profile_visibility, "value") else str(profile.profile_visibility)
    
    return {
        "id": str(user.id),
        "email": user.email,
        "firstName": first_name,
        "lastName": last_name,
        "major": major,
        "minor": minor,
        "bio": bio,
        "profilePhotoKey": profile_photo_key,
        "bannerPhotoKey": banner_photo_key,
        "profileVisibility": profile_visibility,
        "profilePhotoUrl": generate_download_url(profile_photo_key) if profile_photo_key else None,
        "bannerPhotoUrl": generate_download_url(banner_photo_key) if banner_photo_key else None,
    }


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
        
    if payload.profilePhotoKey is not None:
        if payload.profilePhotoKey:
            if not file_exists(payload.profilePhotoKey):
                raise HTTPException(status_code=400, detail="profilePhotoKey does not reference an uploaded file")
            profile.profile_photo_url = normalize_image_name(payload.profilePhotoKey)
        else:
            profile.profile_photo_url = None
            
    if payload.bannerPhotoKey is not None:
        if payload.bannerPhotoKey:
            if not file_exists(payload.bannerPhotoKey):
                raise HTTPException(status_code=400, detail="bannerPhotoKey does not reference an uploaded file")
            profile.banner_photo_url = normalize_image_name(payload.bannerPhotoKey)
        else:
            profile.banner_photo_url = None

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

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
    except ValueError:
        profile.profile_visibility = ProfileVisibility.public

    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    return await get_my_profile_service(user, db)


