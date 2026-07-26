from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.notifications.db_models import (
    Notification,
    NotificationPreference,
    NotificationType,
)
from common.time import utc_now

DEFAULT_CATEGORY_PREFERENCES: dict[str, bool] = {
    "CONNECTION_REQUEST": True,
    "CONNECTION_ACCEPTED": True,
    "DIRECT_MESSAGE": True,
    "ANNOUNCEMENT": True,
    "TOPIC": True,
}


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
    stmt = (
        select(Notification)
        .where(
            Notification.id == notification_id,
            Notification.recipient_user_id == recipient_user_id,
        )
        .options(selectinload(Notification.notification_type))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def count_notifications_for_user(
    db: AsyncSession,
    recipient_user_id: UUID,
    *,
    unread_only: bool = False,
) -> int:
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.recipient_user_id == recipient_user_id)
    )
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    return int((await db.execute(stmt)).scalar_one())


async def list_notifications_for_user(
    db: AsyncSession,
    recipient_user_id: UUID,
    *,
    unread_only: bool = False,
    page: int | None = None,
    page_size: int | None = None,
) -> list[Notification]:
    stmt = (
        select(Notification)
        .where(Notification.recipient_user_id == recipient_user_id)
        .options(selectinload(Notification.notification_type))
        .order_by(Notification.created_at.desc())
    )
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
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
    """Bulk-insert in-app notification rows. Returns inserted count."""
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
    now = read_at or utc_now()
    stmt = (
        update(Notification)
        .where(
            Notification.recipient_user_id == recipient_user_id,
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


async def create_preferences(
    db: AsyncSession,
    *,
    user_id: UUID,
    push_enabled: bool = True,
    in_app_enabled: bool = True,
    category_preferences: dict[str, Any] | None = None,
) -> NotificationPreference:
    preference = NotificationPreference(
        user_id=user_id,
        push_enabled=push_enabled,
        in_app_enabled=in_app_enabled,
        category_preferences=category_preferences or dict(DEFAULT_CATEGORY_PREFERENCES),
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
) -> NotificationPreference:
    if push_enabled is not None:
        preference.push_enabled = push_enabled
    if in_app_enabled is not None:
        preference.in_app_enabled = in_app_enabled
    if category_preferences is not None:
        preference.category_preferences = category_preferences
    preference.updated_at = utc_now()
    db.add(preference)
    await db.flush()
    await db.refresh(preference)
    return preference
