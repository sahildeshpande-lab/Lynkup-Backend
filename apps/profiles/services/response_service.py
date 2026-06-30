from __future__ import annotations
from core.images import generate_download_url
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User
from common.enums import OnboardingStatus, EducationLevel

def _normalize_name_part(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""

def _compose_full_name(first_name: str | None, last_name: str | None) -> str:
    parts = [_normalize_name_part(first_name), _normalize_name_part(last_name)]
    return " ".join(part for part in parts if part).strip()

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

    interest_details: list[dict] = []
    if interests is None:
        interests = []
        if profile and profile.profile_interests_id:
            try:
                id_list = [int(u) for u in profile.profile_interests_id if u is not None]
                if id_list:
                    stmt = select(AcademicInterest.id, AcademicInterest.name).where(AcademicInterest.id.in_(id_list))
                    rows = (await db.execute(stmt)).all()
                    interests = [row.name for row in rows]
                    interest_details = [row.id for row in rows]
            except Exception:
                pass
    else:
        # interests were passed in as names — details stay empty unless rebuilt
        interest_details = []

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
        "loginType": user.registration_type.value if hasattr(user.registration_type, "value") else str(user.registration_type),
        "profilePhoto_url": generate_download_url(profile.profile_photo_url) if (profile and profile.profile_photo_url) else None,
        "bannerPhotoUrl": generate_download_url(profile.banner_photo_url) if (profile and profile.banner_photo_url) else None,
        "status": user.status.value if hasattr(user.status, "value") else str(user.status),
        "university": university_name if university_name is not None else (str(profile.university_id) if (profile and profile.university_id) else None),
        "university_details": {
			"id": profile.university_id if profile else None,
			"university_name": university_name
		},
        "major": profile.major if profile else None,
        "minor": profile.minor if profile else None,
        "county": country_name or "",
        "country": country_name,
        "educationLevel": profile.edu_level if profile else None,
        "educationLevel_details": (
            {
                "id": EducationLevel(profile.edu_level).id,
                "edu_level": profile.edu_level,
            }
            if (profile and profile.edu_level)
            else None
        ),
        "bio": profile.bio if profile else None,
        "academicInterests": interests,
        "academicInterests_details": interest_details,
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
        "posts_count": profile.posts_count if profile else 0,
        "followers_count": profile.followers_count if profile else 0,
        "following_count": profile.following_count if profile else 0,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "updatedAt": user.updated_at.isoformat() if user.updated_at else None,
        "is_onboarding_completed": user.onboarding_status == OnboardingStatus.completed if hasattr(user, "onboarding_status") else False,
        "is_deleted": user.is_deleted
    }
