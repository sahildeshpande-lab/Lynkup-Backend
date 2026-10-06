from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.bulk_send.enums import BulkEmailTargetType
from apps.bulk_send.schemas import BulkEmailTarget
from apps.notifications.repositories.campaign_audience_repository import (
    resolve_topic_recipients,
)
from apps.profiles.db_models import Profile
from common.enums import NotificationTargetType, UserStatus
from common.user_visibility import visible_user_filters

_TARGET_TYPE_MAP: dict[BulkEmailTargetType, NotificationTargetType] = {
    BulkEmailTargetType.MAJOR: NotificationTargetType.major,
    BulkEmailTargetType.MINOR: NotificationTargetType.minor,
    BulkEmailTargetType.EDUCATION_LEVEL: NotificationTargetType.education_level,
    BulkEmailTargetType.UNIVERSITY: NotificationTargetType.university,
    BulkEmailTargetType.COUNTRY: NotificationTargetType.country,
    BulkEmailTargetType.INTEREST: NotificationTargetType.interests,
    BulkEmailTargetType.HASHTAG: NotificationTargetType.hashtags,
    BulkEmailTargetType.USER: NotificationTargetType.users,
}


@dataclass(frozen=True)
class BulkEmailAudienceResult:
    """Audience after targeting, with preference opt-outs separated."""

    eligible_user_ids: list[UUID]
    preference_excluded_user_ids: list[UUID]


def _email_eligible_filters() -> list:
    return [
        *visible_user_filters(User),
        User.status == UserStatus.active,
        User.email.is_not(None),
        User.email != "",
    ]


async def resolve_bulk_email_audience(
    db: AsyncSession,
    *,
    targets: list[BulkEmailTarget],
    is_alumni: bool = False,
) -> BulkEmailAudienceResult:
    """
    Resolve distinct user IDs for a bulk email campaign.

    Semantics:
    - ``to_all=true`` targets impose no restriction (skipped).
    - Within a restrictive target, values are OR'd.
    - Across target types, results are AND'd.
    - Empty / all-unrestricted targets → all email-eligible users.
    - ``is_alumni=true`` is a global alumni filter applied last.
    - Users with ``email_preferences.bulk_email=false`` are always excluded
      (listed separately in ``preference_excluded_user_ids``).
    """
    from apps.notifications.email_preferences import EMAIL_PREF_BULK_EMAIL
    from apps.notifications.repositories.notification_repository import (
        filter_users_eligible_for_email_preference,
    )

    restrictive = [target for target in targets if not target.to_all]

    if not restrictive:
        matched_user_ids = await _list_email_eligible_user_ids(db, is_alumni=is_alumni)
    else:
        notification_targets: list[
            tuple[NotificationTargetType, list[str], bool, bool | None]
        ] = [
            (_TARGET_TYPE_MAP[target.type], list(target.values), False, None)
            for target in restrictive
        ]

        matched_user_ids = await resolve_topic_recipients(db, targets=notification_targets)
        if not matched_user_ids:
            return BulkEmailAudienceResult(
                eligible_user_ids=[],
                preference_excluded_user_ids=[],
            )

        matched_user_ids = await _filter_email_eligible_user_ids(
            db,
            matched_user_ids,
            is_alumni=is_alumni,
        )

    eligible_user_ids = await filter_users_eligible_for_email_preference(
        db,
        matched_user_ids,
        preference=EMAIL_PREF_BULK_EMAIL,
    )
    eligible_set = set(eligible_user_ids)
    preference_excluded_user_ids = [
        user_id for user_id in matched_user_ids if user_id not in eligible_set
    ]
    return BulkEmailAudienceResult(
        eligible_user_ids=eligible_user_ids,
        preference_excluded_user_ids=preference_excluded_user_ids,
    )


async def resolve_bulk_email_user_ids(
    db: AsyncSession,
    *,
    targets: list[BulkEmailTarget],
    is_alumni: bool = False,
) -> list[UUID]:
    """Return eligible user IDs only (preference-filtered)."""
    result = await resolve_bulk_email_audience(
        db,
        targets=targets,
        is_alumni=is_alumni,
    )
    return result.eligible_user_ids


def bulk_email_preference_opt_out_message(
    *,
    excluded_count: int,
    email: str | None = None,
) -> str:
    """Admin-facing message when every matched recipient opted out of bulk email."""
    if excluded_count == 1:
        identity = (email or "").strip()
        if identity:
            return (
                f"Bulk email cannot be sent: {identity} has turned off "
                "bulk email notifications"
            )
        return "This user has turned off bulk email notifications"
    return "All selected recipients have turned off bulk email notifications"


async def _list_email_eligible_user_ids(
    db: AsyncSession,
    *,
    is_alumni: bool = False,
) -> list[UUID]:
    stmt = select(User.id).where(*_email_eligible_filters())
    if is_alumni:
        stmt = stmt.join(Profile, Profile.user_id == User.id).where(
            Profile.is_alumni.is_(True)
        )
    stmt = stmt.distinct().order_by(User.id.asc())
    return list((await db.execute(stmt)).scalars().all())


async def _filter_email_eligible_user_ids(
    db: AsyncSession,
    user_ids: list[UUID],
    *,
    is_alumni: bool = False,
) -> list[UUID]:
    if not user_ids:
        return []

    stmt = select(User.id).where(User.id.in_(user_ids), *_email_eligible_filters())
    if is_alumni:
        stmt = stmt.join(Profile, Profile.user_id == User.id).where(
            Profile.is_alumni.is_(True)
        )
    stmt = stmt.distinct()
    matched = set((await db.execute(stmt)).scalars().all())
    # Preserve resolve_topic_recipients order for stable delivery creation.
    return [user_id for user_id in user_ids if user_id in matched]


async def load_users_by_ids_preserving_order(
    db: AsyncSession,
    user_ids: list[UUID],
) -> list[User]:
    """Load users for delivery rows; dedupe by user id and by email address."""
    if not user_ids:
        return []

    rows = list(
        (
            await db.execute(
                select(User).where(
                    User.id.in_(user_ids),
                    *_email_eligible_filters(),
                )
            )
        ).scalars().all()
    )
    by_id = {user.id: user for user in rows}

    result: list[User] = []
    seen_ids: set[UUID] = set()
    seen_emails: set[str] = set()
    for user_id in user_ids:
        if user_id in seen_ids:
            continue
        user = by_id.get(user_id)
        if user is None:
            continue
        email = (user.email or "").strip().lower()
        if not email or email in seen_emails:
            continue
        seen_ids.add(user_id)
        seen_emails.add(email)
        result.append(user)
    return result
