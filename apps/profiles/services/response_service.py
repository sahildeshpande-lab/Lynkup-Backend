from __future__ import annotations
from datetime import date, datetime, timezone
from uuid import UUID

from core.images import generate_profile_image_url
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User
from apps.profiles.graduation_date import format_graduation_date
from common.enums import OnboardingStatus, EducationLevel, format_user_status

def _normalize_name_part(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""

def _compose_full_name(first_name: str | None, last_name: str | None) -> str:
    parts = [_normalize_name_part(first_name), _normalize_name_part(last_name)]
    return " ".join(part for part in parts if part).strip()


def is_graduation_completed(
    graduation_date: date | None,
    *,
    reference_date: date | None = None,
) -> bool:
    """Return True only when graduation_date is set and reference_date is after it."""
    if graduation_date is None:
        return False
    today = reference_date if reference_date is not None else datetime.now(timezone.utc).date()
    return today > graduation_date


def alumni_status(
    graduation_date: date | None,
    *,
    reference_date: date | None = None,
) -> bool | None:
    """None when no graduation date; otherwise whether graduation is completed."""
    if graduation_date is None:
        return None
    return is_graduation_completed(graduation_date, reference_date=reference_date)


def apply_profile_graduation_date(
    profile,
    graduation_date: date | None,
    user=None,
) -> None:
    """Persist graduation date and align alumni flag with completion rule.

    When the date moves into "not completed yet" (future) or is cleared, also
    reset ``user.has_changed_email_after_graduation`` so the post-graduation
    email-update prompt can show again after the next completion.
    """
    profile.graduation_date = graduation_date
    profile.is_alumni = alumni_status(graduation_date)
    if user is not None and profile.is_alumni is not True:
        user.has_changed_email_after_graduation = False


async def _count_owner_visible_posts(db: AsyncSession, user_id: UUID) -> int:
    from apps.engagement.repositories.repost_repository import (
        count_active_reposts_for_user,
    )
    from apps.feed.repositories.post_repository import count_posts_by_state
    from common.enums import OWNER_VISIBLE_POST_STATES

    authored = await count_posts_by_state(
        db,
        state=OWNER_VISIBLE_POST_STATES,
        user_id=user_id,
    )
    # Reposts of publicly visible originals count as the reposter's posts
    # (same as Profile.posts_count cache). Flagged originals are excluded.
    reposts = await count_active_reposts_for_user(db, user_id)
    return (authored or 0) + (reposts or 0)


def _catalog_value(row: object | None, key: str):
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, None)


async def _load_profile_catalog(
    db: AsyncSession,
    profile,
    *,
    university_name: str | None,
    university_website: str | None,
    country_name: str | None,
) -> tuple[str | None, str | None, str | None, int | None, str | None, int | None, str | None]:
    """Load university, country, major, and minor in one round-trip."""
    from apps.profiles.db_models.country_db_model import Country
    from apps.profiles.db_models.major_db_model import Major
    from apps.profiles.db_models.minor_db_model import Minor
    from apps.profiles.db_models.university_db_model import University
    from sqlmodel import select

    major_id = getattr(profile, "major_id", None)
    minor_id = getattr(profile, "minor_id", None)
    need_university = (university_name is None or university_website is None) and bool(
        getattr(profile, "university_id", None)
    )
    need_country = country_name is None and bool(getattr(profile, "country_id", None))
    if not (need_university or need_country or major_id is not None or minor_id is not None):
        return university_name, university_website, country_name, major_id, None, minor_id, None

    stmt = select(
        select(University.name)
        .where(University.id == profile.university_id)
        .scalar_subquery()
        .label("university_name"),
        select(University.website)
        .where(University.id == profile.university_id)
        .scalar_subquery()
        .label("university_website"),
        select(Country.name)
        .where(Country.id == profile.country_id)
        .scalar_subquery()
        .label("country_name"),
        select(Major.id).where(Major.id == major_id).scalar_subquery().label("major_id"),
        select(Major.name).where(Major.id == major_id).scalar_subquery().label("major_name"),
        select(Minor.id).where(Minor.id == minor_id).scalar_subquery().label("minor_id"),
        select(Minor.name).where(Minor.id == minor_id).scalar_subquery().label("minor_name"),
    )
    row = (await db.execute(stmt)).first()
    if university_name is None:
        university_name = _catalog_value(row, "university_name")
    if university_website is None:
        university_website = _catalog_value(row, "university_website")
    if country_name is None:
        country_name = _catalog_value(row, "country_name")
    resolved_major_id = _catalog_value(row, "major_id")
    if resolved_major_id is not None:
        major_id = resolved_major_id
    major_name = _catalog_value(row, "major_name")
    resolved_minor_id = _catalog_value(row, "minor_id")
    if resolved_minor_id is not None:
        minor_id = resolved_minor_id
    minor_name = _catalog_value(row, "minor_name")
    return (
        university_name,
        university_website,
        country_name,
        major_id,
        major_name,
        minor_id,
        minor_name,
    )


async def _resolve_named_catalog(
    db: AsyncSession,
    model,
    raw_value,
) -> tuple[int | None, str | None]:
    """Resolve a legacy major/minor string (numeric id or name) in one query."""
    from sqlalchemy import func
    from sqlmodel import select

    if raw_value is None:
        return None, None
    if isinstance(raw_value, str) and raw_value.strip().isdigit():
        parsed_id = int(raw_value.strip())
        row = (
            await db.execute(select(model.id, model.name).where(model.id == parsed_id))
        ).first()
        if row:
            return row.id, row.name
        return parsed_id, None
    row = (
        await db.execute(
            select(model.id, model.name).where(func.lower(model.name) == str(raw_value).strip().lower())
        )
    ).first()
    if row:
        return row.id, row.name
    return None, raw_value


async def _resolve_posts_count(
    db: AsyncSession,
    *,
    profile_user_id: UUID,
    cached_posts_count: int,
    viewer_user_id: UUID | None,
) -> int:
    """
    Owner sees published + flagged + reinstate authored posts, plus active
    reposts of originals that are still published/reinstated.
    Visitors see the live public count so stale cache cannot drift.
    """
    if viewer_user_id is None or viewer_user_id != profile_user_id:
        from apps.profiles.services.profile_stats_service import (
            count_public_posts_for_user,
        )

        return await count_public_posts_for_user(db, profile_user_id)
    return await _count_owner_visible_posts(db, profile_user_id)


async def build_user_base_response(
    user: User,
    profile: Profile | None,
    db: AsyncSession,
    *,
    university_name: str | None = None,
    university_website: str | None = None,
    country_name: str | None = None,
    interests: list[str] | None = None,
    viewer_user_id: UUID | None = None,
) -> dict:
    from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
    from apps.profiles.db_models.major_db_model import Major
    from apps.profiles.db_models.minor_db_model import Minor
    from sqlmodel import select

    interest_details: list = []
    if interests is None:
        interests = []
        if profile and profile.profile_interests_id:
            try:
                id_list = [int(u) for u in profile.profile_interests_id if u is not None]
                if id_list:
                    stmt = select(AcademicInterest.id, AcademicInterest.name).where(
                        AcademicInterest.id.in_(id_list)
                    )
                    rows = (await db.execute(stmt)).all()
                    interests = [row.name for row in rows]
                    interest_details = [row.id for row in rows]
            except Exception:  # nosec B110 -- best-effort interest lookup
                pass
    else:
        interest_details = []

    major_name: str | None = None
    minor_name: str | None = None
    major_id: int | None = getattr(profile, "major_id", None) if profile else None
    minor_id: int | None = getattr(profile, "minor_id", None) if profile else None

    if profile is not None:
        try:
            (
                university_name,
                university_website,
                country_name,
                major_id,
                major_name,
                minor_id,
                minor_name,
            ) = await _load_profile_catalog(
                db,
                profile,
                university_name=university_name,
                university_website=university_website,
                country_name=country_name,
            )
        except Exception:  # nosec B110 -- best-effort catalog lookup
            pass

        raw_major = getattr(profile, "major", None)
        if major_name is None and raw_major:
            try:
                resolved_id, resolved_name = await _resolve_named_catalog(db, Major, raw_major)
                if resolved_id is not None:
                    major_id = resolved_id
                major_name = resolved_name
            except Exception:
                major_name = raw_major if not (
                    isinstance(raw_major, str) and raw_major.strip().isdigit()
                ) else major_name
                if isinstance(raw_major, str) and raw_major.strip().isdigit() and major_id is None:
                    major_id = int(raw_major.strip())

        raw_minor = getattr(profile, "minor", None)
        if minor_name is None and raw_minor:
            try:
                resolved_id, resolved_name = await _resolve_named_catalog(db, Minor, raw_minor)
                if resolved_id is not None:
                    minor_id = resolved_id
                minor_name = resolved_name
            except Exception:
                minor_name = raw_minor if not (
                    isinstance(raw_minor, str) and raw_minor.strip().isdigit()
                ) else minor_name
                if isinstance(raw_minor, str) and raw_minor.strip().isdigit() and minor_id is None:
                    minor_id = int(raw_minor.strip())

    first_name = profile.first_name if profile and profile.first_name else ""
    last_name = profile.last_name if profile and profile.last_name else ""

    profile_visibility = "private"
    if profile and profile.profile_visibility:
        profile_visibility = profile.profile_visibility.value if hasattr(profile.profile_visibility, "value") else str(profile.profile_visibility)

    from apps.profiles.services.profile_stats_service import get_connection_count_for_profile
    connection_count = await get_connection_count_for_profile(db, user.id)

    return {
        "id": str(user.id),
        "firstName": first_name,
        "lastName": last_name,
        "email": user.email,
        "role": user.role,
        "loginType": user.registration_type.value if hasattr(user.registration_type, "value") else str(user.registration_type),
        "profilePhoto_url": generate_profile_image_url(profile.profile_photo_url) if (profile and profile.profile_photo_url) else None,
        "bannerPhotoUrl": generate_profile_image_url(profile.banner_photo_url) if (profile and profile.banner_photo_url) else None,
        "status": format_user_status(user.status),
        "university": university_name if university_name is not None else (str(profile.university_id) if (profile and profile.university_id) else None),
        "university_details": {
			"id": profile.university_id if profile else None,
			"university_name": university_name,
			"university_website": university_website,
		},
        "major": major_name,
        "major_details": {
            "id": major_id,
            "major_name": major_name,
        },
        "minor": minor_name,
        "minor_details": {
            "id": minor_id,
            "minor_name": minor_name,
        },
        "country": str(profile.country_id) if (profile and profile.country_id) else None,
        "country_details": {
            "id": profile.country_id if profile else None,
            "country_name": country_name,
        },
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
        "graduationDate": format_graduation_date(profile.graduation_date if profile else None),
        "is_graduation_completed": is_graduation_completed(
            profile.graduation_date if profile else None
        ),
        "is_alumni": alumni_status(
            profile.graduation_date if profile else None
        ),
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
        "has_changed_email_after_graduation": bool(
            getattr(user, "has_changed_email_after_graduation", False)
        ),
        "onlinePresence": profile.online_presence_visible if profile else False,
        "posts_count": await _resolve_posts_count(
            db,
            profile_user_id=user.id,
            cached_posts_count=profile.posts_count if profile else 0,
            viewer_user_id=viewer_user_id,
        ),
        "followers_count": profile.followers_count if profile else 0,
        "following_count": profile.following_count if profile else 0,
        "connection_count": connection_count,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
        "updatedAt": user.updated_at.isoformat() if user.updated_at else None,
        "is_onboarding_completed": user.onboarding_status == OnboardingStatus.completed if hasattr(user, "onboarding_status") else False,
        "is_deleted": user.is_deleted
    }
