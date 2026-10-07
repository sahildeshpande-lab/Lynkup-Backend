from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import cast, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User, UserInstallation
from apps.feed.db_models import Hashtag, Post, PostHashtag
from apps.notifications.db_models import NotificationCampaignAudience
from apps.profiles.db_models import Country, Profile
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.university_db_model import University
from common.enums import EducationLevel, FEED_VISIBLE_POST_STATES, NotificationTargetType, UserStatus
from common.time import utc_now
from common.user_visibility import visible_user_filters


def _try_parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value.strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _normalize_hashtag(value: str) -> str:
    return value.strip().lstrip("#").lower()


def _resolve_edu_level(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    if raw.isdigit():
        try:
            return EducationLevel.from_id(int(raw)).value
        except ValueError:
            return None
    aliases = {
        "undergraduate": "Bachelors",
        "undergrad": "Bachelors",
        "bachelor": "Bachelors",
        "bachelors": "Bachelors",
        "graduate": "Masters",
        "masters": "Masters",
        "master": "Masters",
        "phd": "Doctorate",
        "ph.d": "Doctorate",
        "ph.d.": "Doctorate",
        "doctorate": "Doctorate",
        "doctoral": "Doctorate",
        "postdoctoral": "Postdoctoral",
        "postdoc": "Postdoctoral",
        "jd": "JD",
        "md": "MD",
    }
    alias = aliases.get(raw.lower())
    if alias:
        return alias
    for level in EducationLevel:
        if level.value.lower() == raw.lower() or level.name.lower() == raw.lower():
            return level.value
    return raw


def _profile_interest_contains(interest_id: int):
    """
    Match interest id in profile_interests_id JSON array.

    Use explicit ``@>`` — ``coalesce(...).contains(...)`` can lose the JSONB
    comparator and generate a non-``@>`` predicate, missing valid recipients.
    """
    iid = int(interest_id)
    interests_json = func.coalesce(
        cast(Profile.profile_interests_id, JSONB),
        cast(text("'[]'"), JSONB),
    )
    as_int = cast(text(f"'[{iid}]'"), JSONB)
    as_str = cast(text(f"'[\"{iid}\"]'"), JSONB)
    return or_(
        interests_json.op("@>")(as_int),
        interests_json.op("@>")(as_str),
    )


async def create_campaign_audience(
    db: AsyncSession,
    *,
    campaign_id: UUID,
    user_ids: list[UUID],
) -> int:
    """Bulk-insert audience rows for a campaign. Returns inserted count."""
    if not user_ids:
        return 0

    now = utc_now()
    unique_user_ids = list(dict.fromkeys(user_ids))
    rows = [
        NotificationCampaignAudience(
            id=uuid4(),
            campaign_id=campaign_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        for user_id in unique_user_ids
    ]
    db.add_all(rows)
    await db.flush()
    return len(rows)


async def get_campaign_audience_for_user(
    db: AsyncSession,
    user_id: UUID,
    campaign_ids: list[UUID],
) -> dict[UUID, NotificationCampaignAudience]:
    """Return audience rows keyed by campaign_id for the given user."""
    if not campaign_ids:
        return {}

    unique_campaign_ids = list(dict.fromkeys(campaign_ids))
    stmt = select(NotificationCampaignAudience).where(
        NotificationCampaignAudience.user_id == user_id,
        NotificationCampaignAudience.campaign_id.in_(unique_campaign_ids),
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return {row.campaign_id: row for row in rows}


async def get_campaign_audience_for_users(
    db: AsyncSession,
    user_ids: list[UUID],
    campaign_ids: list[UUID],
) -> dict[tuple[UUID, UUID], NotificationCampaignAudience]:
    """Return audience rows keyed by ``(user_id, campaign_id)``."""
    if not user_ids or not campaign_ids:
        return {}

    unique_user_ids = list(dict.fromkeys(user_ids))
    unique_campaign_ids = list(dict.fromkeys(campaign_ids))
    stmt = select(NotificationCampaignAudience).where(
        NotificationCampaignAudience.user_id.in_(unique_user_ids),
        NotificationCampaignAudience.campaign_id.in_(unique_campaign_ids),
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return {(row.user_id, row.campaign_id): row for row in rows}


async def mark_campaign_audience_read(
    db: AsyncSession,
    *,
    user_id: UUID,
    campaign_id: UUID,
) -> NotificationCampaignAudience:
    """Mark a campaign as read for one user, creating an audience row if needed."""
    stmt = select(NotificationCampaignAudience).where(
        NotificationCampaignAudience.user_id == user_id,
        NotificationCampaignAudience.campaign_id == campaign_id,
    )
    audience = (await db.execute(stmt)).scalar_one_or_none()
    now = utc_now()
    if audience is None:
        audience = NotificationCampaignAudience(
            campaign_id=campaign_id,
            user_id=user_id,
            is_read=True,
            read_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(audience)
    else:
        audience.is_read = True
        audience.read_at = now
        audience.updated_at = now
        db.add(audience)
    await db.flush()
    await db.refresh(audience)
    return audience


async def mark_all_campaign_audience_read(
    db: AsyncSession,
    *,
    user_id: UUID,
    campaign_ids: list[UUID],
) -> int:
    """Mark every listed campaign as read for the user, upserting audience rows."""
    if not campaign_ids:
        return 0

    now = utc_now()
    existing = await get_campaign_audience_for_user(db, user_id, campaign_ids)
    updated = 0
    for campaign_id in dict.fromkeys(campaign_ids):
        audience = existing.get(campaign_id)
        if audience is None:
            db.add(
                NotificationCampaignAudience(
                    campaign_id=campaign_id,
                    user_id=user_id,
                    is_read=True,
                    read_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            updated += 1
            continue
        if not audience.is_read:
            audience.is_read = True
            audience.read_at = now
            audience.updated_at = now
            db.add(audience)
            updated += 1
    if updated:
        await db.flush()
    return updated


async def list_campaign_audience_user_ids(
    db: AsyncSession,
    campaign_id: UUID,
) -> list[UUID]:
    stmt = select(NotificationCampaignAudience.user_id).where(
        NotificationCampaignAudience.campaign_id == campaign_id
    )
    return list((await db.execute(stmt)).scalars().all())


async def resolve_announcement_recipients(db: AsyncSession) -> list[UUID]:
    """All active, visible standard users (role 'user') for ANNOUNCEMENT campaigns."""
    from apps.accounts.db_models import Role, UserRole

    stmt = (
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            *visible_user_filters(User),
            User.status == UserStatus.active,
            Role.name == "user",
        )
        .distinct()
        .order_by(User.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def resolve_topic_recipients(
    db: AsyncSession,
    *,
    targets: list[tuple[NotificationTargetType, list[str]]]
    | list[tuple[NotificationTargetType, list[str], bool]]
    | list[tuple[NotificationTargetType, list[str], bool, bool | None]],
) -> list[UUID]:
    """
    Resolve recipients for TOPIC campaigns.

    - Within the same filter type, values are OR'd (any match).
    - If to_all is True for a filter type, all active users for that filter type match.
    - Across different filter types, results are AND'd (must match every type).
    - USERS + to_all + is_alumni=true restricts to profiles with is_alumni set.

    Examples:
    - University A + University B → users at A or B
    - University (to_all=True) → all users with a university
    - University A + Major B → users at A with major B
    - University A + Major A + Major B → users at A with major A or B
    - USERS (to_all=True, is_alumni=True) → all alumni
    """
    if not targets:
        return []

    # Merge values and to_all flags for the same type so duplicate type entries still OR together.
    merged_values: dict[NotificationTargetType, list[str]] = {}
    to_all_flags: dict[NotificationTargetType, bool] = {}
    alumni_flags: dict[NotificationTargetType, bool | None] = {}
    type_order: list[NotificationTargetType] = []

    for item in targets:
        target_type = item[0]
        target_values = item[1]
        to_all = bool(item[2]) if len(item) >= 3 else False
        is_alumni = item[3] if len(item) >= 4 else None

        if target_type not in merged_values:
            merged_values[target_type] = []
            to_all_flags[target_type] = False
            alumni_flags[target_type] = None
            type_order.append(target_type)

        if to_all:
            to_all_flags[target_type] = True
        if is_alumni is True:
            alumni_flags[target_type] = True

        cleaned = [
            str(value).strip()
            for value in (target_values or [])
            if value and str(value).strip()
        ]
        existing = merged_values[target_type]
        seen_values = {value.casefold() for value in existing}
        for value in cleaned:
            if value.casefold() in seen_values:
                continue
            seen_values.add(value.casefold())
            existing.append(value)

    if not type_order:
        return []

    intersection: set[UUID] | None = None
    ordered_seed: list[UUID] = []

    for index, target_type in enumerate(type_order):
        resolve_kwargs: dict = {}
        if to_all_flags[target_type]:
            resolve_kwargs["to_all"] = True
        if alumni_flags[target_type] is not None:
            resolve_kwargs["is_alumni"] = alumni_flags[target_type]
        recipients = await _resolve_single_target_recipients(
            db,
            target_type=target_type,
            target_values=merged_values[target_type],
            **resolve_kwargs,
        )
        recipient_set = set(recipients)
        if index == 0:
            ordered_seed = recipients
            intersection = recipient_set
        else:
            assert intersection is not None  # nosec B101 -- internal invariant, not input validation
            intersection &= recipient_set

        # AND short-circuit: no users can satisfy remaining filters.
        if not intersection:
            return []

    assert intersection is not None  # nosec B101 -- internal invariant, not input validation
    return [user_id for user_id in ordered_seed if user_id in intersection]


async def get_active_fcm_tokens_for_users(
    db: AsyncSession,
    user_ids: list[UUID],
) -> list[str]:
    """
    Return distinct non-empty FCM tokens for active installations.

    iOS installations store an APNs device token in ``fcm_token`` and are
    excluded here — this helper is for Firebase (topic subscribe / FCM) only.
    Use ``get_active_push_targets_for_users`` when sending pushes.
    """
    if not user_ids:
        return []

    stmt = (
        select(UserInstallation.fcm_token, UserInstallation.platform)
        .where(
            UserInstallation.user_id.in_(user_ids),
            UserInstallation.is_active.is_(True),
            UserInstallation.fcm_token.is_not(None),
        )
    )
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token, platform in (await db.execute(stmt)).all():
        normalized_platform = (platform or "").strip().lower()
        if normalized_platform in {"ios", "iphone", "ipad"}:
            continue
        token = (raw_token or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


async def get_active_push_targets_for_users(
    db: AsyncSession,
    user_ids: list[UUID],
) -> list[tuple[str, str | None]]:
    """
    Return distinct ``(fcm_token, platform)`` pairs for active installations.

    ``fcm_token`` holds an FCM registration token on Android and an APNs device
    token on iOS. Platform may be ``None`` for legacy rows (treated as Android).
    """
    if not user_ids:
        return []

    stmt = (
        select(UserInstallation.fcm_token, UserInstallation.platform)
        .where(
            UserInstallation.user_id.in_(user_ids),
            UserInstallation.is_active.is_(True),
            UserInstallation.fcm_token.is_not(None),
        )
    )
    targets: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for raw_token, platform in (await db.execute(stmt)).all():
        token = (raw_token or "").strip()
        if not token:
            continue
        normalized = (platform or "").strip().lower() or "android"
        key = (normalized, token)
        if key in seen:
            continue
        seen.add(key)
        targets.append((token, platform))
    return targets


async def get_active_push_targets_grouped_by_user(
    db: AsyncSession,
    user_ids: list[UUID],
) -> dict[UUID, list[tuple[str, str | None]]]:
    """
    Return active push targets grouped by ``user_id``.

    Used for campaign fan-out so each user receives their own unread badge.
    """
    if not user_ids:
        return {}

    unique_user_ids = list(dict.fromkeys(user_ids))
    stmt = (
        select(
            UserInstallation.user_id,
            UserInstallation.fcm_token,
            UserInstallation.platform,
        )
        .where(
            UserInstallation.user_id.in_(unique_user_ids),
            UserInstallation.is_active.is_(True),
            UserInstallation.fcm_token.is_not(None),
        )
    )
    grouped: dict[UUID, list[tuple[str, str | None]]] = {
        user_id: [] for user_id in unique_user_ids
    }
    seen: set[tuple[UUID, str, str]] = set()
    for user_id, raw_token, platform in (await db.execute(stmt)).all():
        token = (raw_token or "").strip()
        if not token or user_id is None:
            continue
        normalized = (platform or "").strip().lower() or "android"
        key = (user_id, normalized, token)
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(user_id, []).append((token, platform))
    return {user_id: targets for user_id, targets in grouped.items() if targets}


def _alumni_filters(is_alumni: bool | None) -> list:
    """Alumni-only when ``is_alumni`` is true; omitted or false applies no alumni filter."""
    if is_alumni is not True:
        return []
    return [Profile.is_alumni.is_(True)]


async def _resolve_single_target_recipients(
    db: AsyncSession,
    *,
    target_type: NotificationTargetType,
    target_values: list[str],
    to_all: bool = False,
    is_alumni: bool | None = None,
) -> list[UUID]:
    alumni_extra = _alumni_filters(is_alumni)

    if target_type == NotificationTargetType.users:
        if to_all:
            return await _base_visible_profile_user_ids(db, alumni_extra)
        return await _resolve_explicit_users(db, target_values, alumni_extra)

    if to_all:
        if target_type == NotificationTargetType.university:
            return await _base_visible_profile_user_ids(
                db, [Profile.university_id.is_not(None), *alumni_extra]
            )
        if target_type == NotificationTargetType.major:
            return await _base_visible_profile_user_ids(
                db, [Profile.major.is_not(None), func.trim(Profile.major) != "", *alumni_extra]
            )
        if target_type == NotificationTargetType.minor:
            return await _base_visible_profile_user_ids(
                db, [Profile.minor.is_not(None), func.trim(Profile.minor) != "", *alumni_extra]
            )
        if target_type == NotificationTargetType.education_level:
            return await _base_visible_profile_user_ids(
                db, [Profile.edu_level.is_not(None), *alumni_extra]
            )
        if target_type == NotificationTargetType.country:
            return await _base_visible_profile_user_ids(
                db, [Profile.country_id.is_not(None), *alumni_extra]
            )
        if target_type == NotificationTargetType.interests:
            return await _base_visible_profile_user_ids(
                db, [Profile.profile_interests_id.is_not(None), *alumni_extra]
            )
        if target_type == NotificationTargetType.hashtags:
            return await _base_visible_profile_user_ids(db, alumni_extra)
        return []

    cleaned = [str(value).strip() for value in target_values if value and str(value).strip()]
    if not cleaned:
        return []

    if target_type == NotificationTargetType.university:
        return await _resolve_university_users(db, cleaned)
    if target_type == NotificationTargetType.major:
        return await _resolve_major_users(db, cleaned)
    if target_type == NotificationTargetType.minor:
        return await _resolve_minor_users(db, cleaned)
    if target_type == NotificationTargetType.education_level:
        return await _resolve_education_level_users(db, cleaned)
    if target_type == NotificationTargetType.country:
        return await _resolve_country_users(db, cleaned)
    if target_type == NotificationTargetType.interests:
        return await _resolve_interest_users(db, cleaned)
    if target_type == NotificationTargetType.hashtags:
        return await _resolve_hashtag_users(db, cleaned)
    return []


async def _resolve_explicit_users(
    db: AsyncSession,
    values: list[str],
    extra_filters: list,
) -> list[UUID]:
    user_ids = [
        parsed
        for parsed in (_try_parse_uuid(value) for value in values)
        if parsed is not None
    ]
    if not user_ids:
        return []
    return await _base_visible_profile_user_ids(
        db,
        [Profile.user_id.in_(user_ids), *extra_filters],
    )


async def _base_visible_profile_user_ids(db: AsyncSession, extra_filters: list) -> list[UUID]:
    stmt = (
        select(Profile.user_id)
        .join(User, User.id == Profile.user_id)
        .where(*visible_user_filters(User), User.status == UserStatus.active, *extra_filters)
        .distinct()
    )
    return list((await db.execute(stmt)).scalars().all())


async def _resolve_university_users(db: AsyncSession, values: list[str]) -> list[UUID]:
    id_values: list[UUID] = []
    name_values: list[str] = []
    for value in values:
        university_id = _try_parse_uuid(value)
        if university_id is not None:
            id_values.append(university_id)
        else:
            name_values.append(value)

    clauses = []
    if id_values:
        clauses.append(Profile.university_id.in_(id_values))
    for name in name_values:
        clauses.append(University.name.ilike(name))

    if not clauses:
        return []

    stmt = (
        select(Profile.user_id)
        .join(User, User.id == Profile.user_id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(
            *visible_user_filters(User),
            User.status == UserStatus.active,
            or_(*clauses),
        )
        .distinct()
    )
    return list((await db.execute(stmt)).scalars().all())


async def _resolve_profile_string_users(
    db: AsyncSession,
    column,
    values: list[str],
) -> list[UUID]:
    clauses = [func.lower(func.trim(column)) == value.lower() for value in values]
    return await _base_visible_profile_user_ids(db, [or_(*clauses)])


async def _resolve_major_users(db: AsyncSession, values: list[str]) -> list[UUID]:
    """Match profiles by ``major_id`` and/or major name."""
    from apps.profiles.db_models.major_db_model import Major

    id_values: list[int] = []
    name_values: list[str] = []
    for value in values:
        if value.isdigit():
            id_values.append(int(value))
        else:
            name_values.append(value)

    clauses = []
    if id_values:
        clauses.append(Profile.major_id.in_(id_values))
        name_stmt = select(Major.name).where(Major.id.in_(id_values))
        resolved_names = list((await db.execute(name_stmt)).scalars().all())
        name_values.extend(n for n in resolved_names if n and str(n).strip())

    for name in name_values:
        clauses.append(func.lower(func.trim(Profile.major)) == name.lower())

    if not clauses:
        return []
    return await _base_visible_profile_user_ids(db, [or_(*clauses)])


async def _resolve_minor_users(db: AsyncSession, values: list[str]) -> list[UUID]:
    """Match profiles by ``minor_id`` and/or minor name."""
    from apps.profiles.db_models.minor_db_model import Minor

    id_values: list[int] = []
    name_values: list[str] = []
    for value in values:
        if value.isdigit():
            id_values.append(int(value))
        else:
            name_values.append(value)

    clauses = []
    if id_values:
        clauses.append(Profile.minor_id.in_(id_values))
        name_stmt = select(Minor.name).where(Minor.id.in_(id_values))
        resolved_names = list((await db.execute(name_stmt)).scalars().all())
        name_values.extend(n for n in resolved_names if n and str(n).strip())

    for name in name_values:
        clauses.append(func.lower(func.trim(Profile.minor)) == name.lower())

    if not clauses:
        return []
    return await _base_visible_profile_user_ids(db, [or_(*clauses)])


async def _resolve_education_level_users(db: AsyncSession, values: list[str]) -> list[UUID]:
    resolved: list[str] = []
    seen: set[str] = set()
    for value in values:
        level = _resolve_edu_level(value)
        if not level:
            continue
        key = level.lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(level)
    if not resolved:
        return []
    clauses = [
        func.lower(func.trim(Profile.edu_level)) == level.lower() for level in resolved
    ]
    return await _base_visible_profile_user_ids(db, [or_(*clauses)])


async def _resolve_country_users(db: AsyncSession, values: list[str]) -> list[UUID]:
    id_values: list[UUID] = []
    name_or_code_values: list[str] = []
    for value in values:
        country_id = _try_parse_uuid(value)
        if country_id is not None:
            id_values.append(country_id)
        else:
            name_or_code_values.append(value)

    clauses = []
    if id_values:
        clauses.append(Profile.country_id.in_(id_values))
    for term in name_or_code_values:
        clauses.append(
            or_(
                func.lower(func.trim(Country.name)) == term.lower(),
                Country.iso_code.ilike(term),
            )
        )
    if not clauses:
        return []

    stmt = (
        select(Profile.user_id)
        .join(User, User.id == Profile.user_id)
        .outerjoin(Country, Country.id == Profile.country_id)
        .where(
            *visible_user_filters(User),
            User.status == UserStatus.active,
            or_(*clauses),
        )
        .distinct()
    )
    return list((await db.execute(stmt)).scalars().all())


async def _resolve_interest_users(db: AsyncSession, values: list[str]) -> list[UUID]:
    interest_ids: list[int] = []
    name_values: list[str] = []
    for value in values:
        if value.isdigit():
            interest_ids.append(int(value))
        else:
            name_values.append(value)

    if name_values:
        name_stmt = select(AcademicInterest.id).where(
            or_(
                *[
                    func.lower(func.trim(AcademicInterest.name)) == name.lower()
                    for name in name_values
                ]
            )
        )
        interest_ids.extend(int(row) for row in (await db.execute(name_stmt)).scalars().all())

    interest_ids = list(dict.fromkeys(interest_ids))
    if not interest_ids:
        return []

    clauses = [_profile_interest_contains(interest_id) for interest_id in interest_ids]
    return await _base_visible_profile_user_ids(db, [or_(*clauses)])


async def _resolve_hashtag_tags(db: AsyncSession, values: list[str]) -> list[str]:
    tags: list[str] = []
    hashtag_ids: list[UUID] = []

    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        parsed_id = _try_parse_uuid(raw)
        if parsed_id is not None:
            hashtag_ids.append(parsed_id)
            continue
        normalized = _normalize_hashtag(raw)
        if normalized:
            tags.append(normalized)

    if hashtag_ids:
        rows = (
            await db.execute(select(Hashtag.tag).where(Hashtag.id.in_(hashtag_ids)))
        ).scalars().all()
        tags.extend((tag or "").strip().lower() for tag in rows if (tag or "").strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        deduped.append(tag)
    return deduped


async def _resolve_hashtag_users(db: AsyncSession, values: list[str]) -> list[UUID]:
    tags = await _resolve_hashtag_tags(db, values)
    if not tags:
        return []

    stmt = (
        select(Post.author_user_id)
        .join(PostHashtag, PostHashtag.post_id == Post.id)
        .join(Hashtag, Hashtag.id == PostHashtag.hashtag_id)
        .join(User, User.id == Post.author_user_id)
        .where(
            *visible_user_filters(User),
            User.status == UserStatus.active,
            Hashtag.tag.in_(tags),
            Post.state.in_(FEED_VISIBLE_POST_STATES),
        )
        .distinct()
    )
    return list((await db.execute(stmt)).scalars().all())
