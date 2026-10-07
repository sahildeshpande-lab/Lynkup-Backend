from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from apps.notifications.db_models import (
    Notification,
    NotificationCategory,
    NotificationCampaign,
    NotificationPreference,
    NotificationType,
)
from common.time import utc_now


async def list_active_notification_categories(
    db: AsyncSession,
) -> list[NotificationCategory]:
    stmt = (
        select(NotificationCategory)
        .where(NotificationCategory.is_active.is_(True))
        .order_by(NotificationCategory.code.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_active_notification_type_names(db: AsyncSession) -> list[str]:
    stmt = (
        select(NotificationType.name)
        .where(NotificationType.is_active.is_(True))
        .order_by(NotificationType.name.asc())
    )
    return [str(name) for name in (await db.execute(stmt)).scalars().all() if name]


async def get_default_category_preferences(db: AsyncSession) -> dict[str, bool]:
    """
    Build `{code: True}` for every active preference category.

    Prefer ``notification_categories``. If that catalog is empty (not seeded yet),
    fall back to active ``notification_types`` so GET/PATCH preferences still work.
    """
    categories = await list_active_notification_categories(db)
    if categories:
        return {category.code: True for category in categories}
    return {name: True for name in await list_active_notification_type_names(db)}


async def get_notification_type_by_name(
    db: AsyncSession,
    name: str,
) -> NotificationType | None:
    stmt = select(NotificationType).where(NotificationType.name == name)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_notification_type_by_id(
    db: AsyncSession,
    notification_type_id: UUID,
) -> NotificationType | None:
    stmt = select(NotificationType).where(NotificationType.id == notification_type_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_notification_by_id(
    db: AsyncSession,
    notification_id: UUID,
) -> Notification | None:
    stmt = select(Notification).where(Notification.id == notification_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_notification_for_user(
    db: AsyncSession,
    *,
    notification_id: UUID,
    recipient_user_id: UUID,
) -> Notification | None:
    """Return a personal notification owned by the user."""
    stmt = (
        select(Notification)
        .where(
            Notification.id == notification_id,
            Notification.recipient_user_id == recipient_user_id,
            Notification.campaign_id.is_(None),
        )
        .options(selectinload(Notification.notification_type))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_broadcast_notification_by_id(
    db: AsyncSession,
    notification_id: UUID,
) -> Notification | None:
    stmt = (
        select(Notification)
        .where(
            Notification.id == notification_id,
            Notification.campaign_id.is_not(None),
        )
        .options(
            selectinload(Notification.notification_type),
            selectinload(Notification.campaign),
        )
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_personal_notifications_for_user(
    db: AsyncSession,
    recipient_user_id: UUID,
    *,
    is_read: bool | None = None,
) -> list[Notification]:
    stmt = (
        select(Notification)
        .where(
            Notification.recipient_user_id == recipient_user_id,
            Notification.campaign_id.is_(None),
        )
        .options(selectinload(Notification.notification_type))
        .order_by(Notification.created_at.desc())
    )
    if is_read is not None:
        stmt = stmt.where(Notification.is_read.is_(is_read))
    return list((await db.execute(stmt)).scalars().all())


async def list_broadcast_notifications(
    db: AsyncSession,
) -> list[Notification]:
    """Active campaign broadcast notification rows (ANNOUNCEMENT / TOPIC), newest first."""
    stmt = (
        select(Notification)
        .join(NotificationType, NotificationType.id == Notification.notification_type_id)
        .join(
            NotificationCampaign,
            NotificationCampaign.id == Notification.campaign_id,
        )
        .where(
            Notification.campaign_id.is_not(None),
            NotificationType.name.in_(("ANNOUNCEMENT", "TOPIC")),
            NotificationCampaign.is_active.is_(True),
        )
        .options(
            selectinload(Notification.notification_type),
            # Join already loads campaign columns; populate relationship to avoid
            # async lazy-load (MissingGreenlet) in registration filtering.
            contains_eager(Notification.campaign),
        )
        .order_by(Notification.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_notification_by_campaign_id(
    db: AsyncSession,
    campaign_id: UUID,
) -> Notification | None:
    stmt = select(Notification).where(Notification.campaign_id == campaign_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def update_notification_content(
    db: AsyncSession,
    notification: Notification,
    *,
    title: str,
    body: str,
    deep_link_payload: dict[str, Any] | None = None,
) -> Notification:
    notification.title = title
    notification.body = body
    if deep_link_payload is not None:
        notification.deep_link_payload = deep_link_payload
    notification.updated_at = utc_now()
    db.add(notification)
    await db.flush()
    await db.refresh(notification)
    return notification


async def count_notifications_for_user(
    db: AsyncSession,
    recipient_user_id: UUID,
    *,
    is_read: bool | None = None,
) -> int:
    """Count personal notifications only (broadcast eligibility is computed in the service)."""
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.recipient_user_id == recipient_user_id,
            Notification.campaign_id.is_(None),
        )
    )
    if is_read is not None:
        stmt = stmt.where(Notification.is_read.is_(is_read))
    return int((await db.execute(stmt)).scalar_one())


async def count_unread_personal_by_type_for_users(
    db: AsyncSession,
    user_ids: list[UUID],
) -> dict[UUID, dict[str, int]]:
    """
    Efficient unread personal counts grouped by user and notification type name.

    Only rows with ``campaign_id IS NULL`` and ``is_read = false`` are included.
    Broadcast / preference visibility is applied by the service layer.
    """
    if not user_ids:
        return {}

    unique_user_ids = list(dict.fromkeys(user_ids))
    stmt = (
        select(
            Notification.recipient_user_id,
            NotificationType.name,
            func.count(),
        )
        .select_from(Notification)
        .join(
            NotificationType,
            NotificationType.id == Notification.notification_type_id,
        )
        .where(
            Notification.recipient_user_id.in_(unique_user_ids),
            Notification.campaign_id.is_(None),
            Notification.is_read.is_(False),
        )
        .group_by(Notification.recipient_user_id, NotificationType.name)
    )
    result: dict[UUID, dict[str, int]] = {user_id: {} for user_id in unique_user_ids}
    for user_id, type_name, count in (await db.execute(stmt)).all():
        if user_id is None or not type_name:
            continue
        result.setdefault(user_id, {})[str(type_name)] = int(count or 0)
    return result


async def get_preferences_for_users(
    db: AsyncSession,
    user_ids: list[UUID],
) -> dict[UUID, NotificationPreference]:
    """Return preference rows keyed by user_id for the given users."""
    if not user_ids:
        return {}

    unique_user_ids = list(dict.fromkeys(user_ids))
    stmt = select(NotificationPreference).where(
        NotificationPreference.user_id.in_(unique_user_ids)
    )
    return {
        pref.user_id: pref
        for pref in (await db.execute(stmt)).scalars().all()
    }


async def list_notifications_for_user(
    db: AsyncSession,
    recipient_user_id: UUID,
    *,
    is_read: bool | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[Notification]:
    """Personal inbox rows only. Prefer service-layer merge with broadcasts."""
    stmt = (
        select(Notification)
        .where(
            Notification.recipient_user_id == recipient_user_id,
            Notification.campaign_id.is_(None),
        )
        .options(selectinload(Notification.notification_type))
        .order_by(Notification.created_at.desc())
    )
    if is_read is not None:
        stmt = stmt.where(Notification.is_read.is_(is_read))
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return list((await db.execute(stmt)).scalars().all())


async def create_notification(
    db: AsyncSession,
    *,
    recipient_user_id: UUID,
    notification_type_id: UUID,
    title: str,
    body: str,
    deep_link_payload: dict[str, Any] | None = None,
    campaign_id: UUID | None = None,
) -> Notification:
    notification = Notification(
        recipient_user_id=recipient_user_id,
        notification_type_id=notification_type_id,
        campaign_id=campaign_id,
        title=title,
        body=body,
        deep_link_payload=deep_link_payload,
        is_read=False,
        read_at=None,
    )
    db.add(notification)
    await db.flush()
    await db.refresh(notification)
    return notification


async def create_broadcast_notification(
    db: AsyncSession,
    *,
    owner_user_id: UUID,
    notification_type_id: UUID,
    campaign_id: UUID,
    title: str,
    body: str,
    deep_link_payload: dict[str, Any] | None = None,
) -> Notification:
    """
    Insert the single in-app notification row that represents an admin campaign.

    ``owner_user_id`` satisfies the non-null recipient FK (typically the admin who
    created the campaign). Listing logic treats ``campaign_id IS NOT NULL`` as a
    broadcast and does not treat this as a personal inbox item.
    """
    return await create_notification(
        db,
        recipient_user_id=owner_user_id,
        notification_type_id=notification_type_id,
        title=title,
        body=body,
        deep_link_payload=deep_link_payload,
        campaign_id=campaign_id,
    )


async def bulk_create_notifications(
    db: AsyncSession,
    *,
    recipient_user_ids: list[UUID],
    notification_type_id: UUID,
    campaign_id: UUID,
    title: str,
    body: str,
    deep_link_payload: dict[str, Any] | None = None,
) -> int:
    """Bulk-insert in-app notification rows. Returns inserted count.

    Prefer ``create_broadcast_notification`` for ANNOUNCEMENT/TOPIC campaigns.
    """
    if not recipient_user_ids:
        return 0

    now = utc_now()
    unique_user_ids = list(dict.fromkeys(recipient_user_ids))
    rows = [
        Notification(
            id=uuid4(),
            recipient_user_id=user_id,
            notification_type_id=notification_type_id,
            campaign_id=campaign_id,
            title=title,
            body=body,
            deep_link_payload=deep_link_payload,
            is_read=False,
            read_at=None,
            created_at=now,
            updated_at=now,
        )
        for user_id in unique_user_ids
    ]
    db.add_all(rows)
    await db.flush()
    return len(rows)


async def mark_notification_as_read(
    db: AsyncSession,
    notification: Notification,
    *,
    read_at: datetime | None = None,
) -> Notification:
    notification.is_read = True
    notification.read_at = read_at or utc_now()
    notification.updated_at = utc_now()
    db.add(notification)
    await db.flush()
    await db.refresh(notification)
    return notification


async def mark_all_notifications_read(
    db: AsyncSession,
    recipient_user_id: UUID,
    *,
    read_at: datetime | None = None,
) -> int:
    """Mark personal notifications as read (broadcasts are shared and left unchanged)."""
    now = read_at or utc_now()
    stmt = (
        update(Notification)
        .where(
            Notification.recipient_user_id == recipient_user_id,
            Notification.campaign_id.is_(None),
            Notification.is_read.is_(False),
        )
        .values(is_read=True, read_at=now, updated_at=now)
    )
    result = await db.execute(stmt)
    await db.flush()
    return int(result.rowcount or 0)


async def get_preferences_by_user_id(
    db: AsyncSession,
    user_id: UUID,
) -> NotificationPreference | None:
    stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _merge_category_preferences(
    defaults: dict[str, bool],
    stored: dict[str, Any] | None,
) -> dict[str, bool]:
    """Overlay stored user values onto active category defaults."""
    from apps.notifications.email_preferences import EXTRA_CATEGORY_PREFERENCE_DEFAULTS

    merged = {**defaults, **EXTRA_CATEGORY_PREFERENCE_DEFAULTS}
    if stored:
        for key in list(merged.keys()):
            if key in stored:
                merged[key] = bool(stored[key])
            elif key.lower() in stored:
                merged[key] = bool(stored[key.lower()])
    return merged


async def filter_users_eligible_for_push(
    db: AsyncSession,
    user_ids: list[UUID],
    *,
    category: str,
) -> list[UUID]:
    """
    Return user IDs that should receive push for ``category``.

    Requires ``push_enabled`` and the merged category preference to be true.
    Users without a preference row use catalog defaults (typically all enabled).
    """
    if not user_ids:
        return []

    from apps.notifications.email_preferences import (
        CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER,
    )

    raw = category.strip()
    if raw.upper() == "CONNECTION_REMINDER":
        category_key = CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER
    elif raw.lower() == CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER:
        category_key = CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER
    else:
        category_key = raw.upper()
    defaults = await get_default_category_preferences(db)

    stmt = select(NotificationPreference).where(
        NotificationPreference.user_id.in_(user_ids)
    )
    preferences = {
        pref.user_id: pref
        for pref in (await db.execute(stmt)).scalars().all()
    }

    eligible: list[UUID] = []
    seen: set[UUID] = set()
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)

        pref = preferences.get(user_id)
        if pref is None:
            merged_defaults = _merge_category_preferences(defaults, None)
            if merged_defaults.get(category_key, True):
                eligible.append(user_id)
            continue
        if not pref.push_enabled:
            continue
        merged = _merge_category_preferences(defaults, pref.category_preferences)
        if merged.get(category_key, True):
            eligible.append(user_id)

    return eligible


async def filter_users_eligible_for_email_preference(
    db: AsyncSession,
    user_ids: list[UUID],
    *,
    preference: str,
) -> list[UUID]:
    """
    Return user IDs that have the given email preference enabled.

    Missing preference rows / keys default to enabled (backward compatible).
    """
    from apps.notifications.email_preferences import is_email_preference_enabled

    if not user_ids:
        return []

    stmt = select(NotificationPreference).where(
        NotificationPreference.user_id.in_(user_ids)
    )
    preferences = {
        pref.user_id: pref
        for pref in (await db.execute(stmt)).scalars().all()
    }

    eligible: list[UUID] = []
    seen: set[UUID] = set()
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        if is_email_preference_enabled(preferences.get(user_id), preference):
            eligible.append(user_id)
    return eligible


async def create_preferences(
    db: AsyncSession,
    *,
    user_id: UUID,
    push_enabled: bool = True,
    in_app_enabled: bool = True,
    category_preferences: dict[str, Any] | None = None,
    email_preferences: dict[str, Any] | None = None,
) -> NotificationPreference:
    from apps.notifications.email_preferences import default_email_preferences

    preference = NotificationPreference(
        user_id=user_id,
        push_enabled=push_enabled,
        in_app_enabled=in_app_enabled,
        category_preferences=(
            category_preferences
            if category_preferences is not None
            else await get_default_category_preferences(db)
        ),
        email_preferences=(
            email_preferences
            if email_preferences is not None
            else default_email_preferences()
        ),
    )
    db.add(preference)
    await db.flush()
    await db.refresh(preference)
    return preference


async def update_preferences(
    db: AsyncSession,
    preference: NotificationPreference,
    *,
    push_enabled: bool | None = None,
    in_app_enabled: bool | None = None,
    category_preferences: dict[str, Any] | None = None,
    email_preferences: dict[str, Any] | None = None,
) -> NotificationPreference:
    if push_enabled is not None:
        preference.push_enabled = push_enabled
    if in_app_enabled is not None:
        preference.in_app_enabled = in_app_enabled
    if category_preferences is not None:
        preference.category_preferences = category_preferences
    if email_preferences is not None:
        preference.email_preferences = email_preferences
    preference.updated_at = utc_now()
    db.add(preference)
    await db.flush()
    await db.refresh(preference)
    return preference


async def has_recent_post_recognition_notification(
    db: AsyncSession,
    *,
    recipient_user_id: UUID,
    milestone: int,
    within_seconds: int,
) -> bool:
    """Return True when the user already received this milestone notification recently."""
    if within_seconds <= 0:
        return False

    cutoff_dt = utc_now() - timedelta(seconds=within_seconds)

    stmt = (
        select(Notification.id)
        .join(NotificationType, NotificationType.id == Notification.notification_type_id)
        .where(
            Notification.recipient_user_id == recipient_user_id,
            Notification.campaign_id.is_(None),
            NotificationType.name == "POST_RECOGNITION",
            Notification.created_at >= cutoff_dt,
            Notification.deep_link_payload["milestone"].astext == str(milestone),
        )
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None
