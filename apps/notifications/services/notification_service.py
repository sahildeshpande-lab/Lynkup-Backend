from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.notifications.db_models import Notification
from apps.notifications.repositories.campaign_audience_repository import (
    get_active_fcm_tokens_for_users,
)
from apps.notifications.repositories.notification_repository import (
    create_notification as persist_notification,
    create_preferences,
    get_broadcast_notification_by_id,
    get_default_category_preferences,
    get_notification_for_user,
    get_notification_type_by_name,
    get_preferences_by_user_id,
    list_broadcast_notifications,
    list_personal_notifications_for_user,
    mark_all_notifications_read as persist_mark_all_read,
    mark_notification_as_read as persist_mark_as_read,
    update_preferences as persist_update_preferences,
)
from apps.notifications.schemas import (
    MarkAllNotificationsReadResponse,
    MarkNotificationReadResponse,
    NotificationItem,
    NotificationListResponse,
    NotificationPreferencesData,
    NotificationPreferencesResponse,
    UpdateNotificationPreferencesRequest,
)
from apps.notifications.services.topic_service import TopicService
from common.pagination import build_paginated_response
from common.responses import error_response, success_response
from core.auth.services import send_push_notifications

logger = logging.getLogger(__name__)

_FIREBASE_TOPICS_KEY = "firebase_topics"


async def _merged_category_preferences(
    db: AsyncSession,
    existing: dict[str, Any] | None,
) -> dict[str, bool]:
    """
    Active categories from DB as defaults (True), overlaid with the user's stored
    values. Inactive categories are excluded even if present in stored JSON.
    """
    merged = dict(await get_default_category_preferences(db))
    if existing:
        for key in list(merged.keys()):
            if key in existing:
                merged[key] = bool(existing[key])
    return merged


async def _is_category_enabled(
    db: AsyncSession,
    preferences,
    notification_type: str,
) -> bool:
    categories = await _merged_category_preferences(db, preferences.category_preferences)
    return bool(categories.get(notification_type, True))


def _to_notification_item(
    notification: Notification,
    *,
    is_broadcast: bool = False,
) -> NotificationItem:
    type_name = None
    if getattr(notification, "notification_type", None) is not None:
        type_name = notification.notification_type.name
    # Broadcast rows are shared; per-user read state is not stored without a schema change.
    is_read = False if is_broadcast else notification.is_read
    read_at = None if is_broadcast else notification.read_at
    return NotificationItem(
        id=notification.id,
        notification_type=type_name,
        notification_type_id=notification.notification_type_id,
        campaign_id=notification.campaign_id,
        title=notification.title,
        body=notification.body,
        deep_link_payload=notification.deep_link_payload,
        is_read=is_read,
        read_at=read_at,
        created_at=notification.created_at,
    )


async def _user_topic_set(db: AsyncSession, user_id: UUID) -> set[str]:
    from apps.profiles.db_models.profile_db_model import Profile

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        return set()
    return await TopicService.build_topics(db, profile)


def _is_broadcast_visible_to_user(
    notification: Notification,
    *,
    user_topics: set[str],
) -> bool:
    type_name = None
    if getattr(notification, "notification_type", None) is not None:
        type_name = notification.notification_type.name
    if type_name == "ANNOUNCEMENT":
        return True
    if type_name == "TOPIC":
        stored = (notification.deep_link_payload or {}).get(_FIREBASE_TOPICS_KEY) or []
        return bool(user_topics.intersection(set(stored)))
    return False


async def _list_unified_notifications_for_user(
    db: AsyncSession,
    user_id: UUID,
    *,
    is_read: bool | None = None,
) -> list[tuple[Notification, bool]]:
    """Return (notification, is_broadcast) pairs newest-first, respecting preferences."""
    preference = await _get_or_create_preferences(db, user_id)
    if not preference.in_app_enabled:
        return []

    enabled_categories = await _merged_category_preferences(
        db,
        preference.category_preferences,
    )

    personal = await list_personal_notifications_for_user(
        db,
        user_id,
        is_read=is_read,
    )
    personal = [
        row
        for row in personal
        if _notification_type_enabled(row, enabled_categories)
    ]

    # Broadcast rows have no per-user read state and are always exposed as is_read=false.
    if is_read is True:
        broadcasts: list[Notification] = []
    else:
        broadcasts = await list_broadcast_notifications(db)

    user_topics: set[str] | None = None
    visible_broadcasts: list[Notification] = []
    for notification in broadcasts:
        type_name = (
            notification.notification_type.name
            if getattr(notification, "notification_type", None) is not None
            else None
        )
        if type_name and not enabled_categories.get(type_name, True):
            continue
        if type_name == "TOPIC":
            if user_topics is None:
                user_topics = await _user_topic_set(db, user_id)
            if not _is_broadcast_visible_to_user(
                notification,
                user_topics=user_topics,
            ):
                continue
        elif type_name != "ANNOUNCEMENT":
            continue
        visible_broadcasts.append(notification)

    merged: list[tuple[Notification, bool]] = [
        *((row, False) for row in personal),
        *((row, True) for row in visible_broadcasts),
    ]
    merged.sort(key=lambda item: item[0].created_at, reverse=True)
    return merged


def _notification_type_enabled(
    notification: Notification,
    enabled_categories: dict[str, bool],
) -> bool:
    type_name = None
    if getattr(notification, "notification_type", None) is not None:
        type_name = notification.notification_type.name
    if not type_name:
        return True
    return bool(enabled_categories.get(type_name, True))


async def _get_or_create_preferences(db: AsyncSession, user_id: UUID):
    preference = await get_preferences_by_user_id(db, user_id)
    if preference is not None:
        # Ensure newly added active categories appear; drop deactivated from the view.
        merged = await _merged_category_preferences(db, preference.category_preferences)
        if merged != (preference.category_preferences or {}):
            preference = await persist_update_preferences(
                db,
                preference,
                category_preferences=merged,
            )
        return preference

    defaults = await get_default_category_preferences(db)
    return await create_preferences(
        db,
        user_id=user_id,
        category_preferences=defaults,
    )


async def get_preferences(
    db: AsyncSession,
    *,
    user_id: UUID,
) -> NotificationPreferencesResponse:
    preference = await _get_or_create_preferences(db, user_id)
    await db.commit()
    return success_response(
        "Notification preferences fetched successfully.",
        NotificationPreferencesData(
            push_enabled=preference.push_enabled,
            in_app_enabled=preference.in_app_enabled,
            category_preferences=await _merged_category_preferences(
                db,
                preference.category_preferences,
            ),
        ),
        response_cls=NotificationPreferencesResponse,
    )


async def update_preferences(
    db: AsyncSession,
    *,
    user_id: UUID,
    payload: UpdateNotificationPreferencesRequest,
) -> NotificationPreferencesResponse:
    preference = await _get_or_create_preferences(db, user_id)

    merged_categories = None
    if payload.category_preferences is not None:
        merged_categories = await _merged_category_preferences(
            db,
            preference.category_preferences,
        )
        for key, value in payload.category_preferences.items():
            key_str = str(key).strip().upper()
            if key_str in merged_categories:
                merged_categories[key_str] = bool(value)

    preference = await persist_update_preferences(
        db,
        preference,
        push_enabled=payload.push_enabled,
        in_app_enabled=payload.in_app_enabled,
        category_preferences=merged_categories,
    )
    await db.commit()
    await db.refresh(preference)

    return success_response(
        "Notification preferences updated successfully.",
        NotificationPreferencesData(
            push_enabled=preference.push_enabled,
            in_app_enabled=preference.in_app_enabled,
            category_preferences=await _merged_category_preferences(
                db,
                preference.category_preferences,
            ),
        ),
        response_cls=NotificationPreferencesResponse,
    )


async def list_notifications(
    db: AsyncSession,
    *,
    user_id: UUID,
    page: int | None = None,
    page_size: int | None = None,
    is_read: bool | None = None,
) -> NotificationListResponse:
    merged = await _list_unified_notifications_for_user(
        db,
        user_id,
        is_read=is_read,
    )
    total_items = len(merged)

    paginate = page is not None or page_size is not None
    if paginate:
        resolved_page = page if page is not None else 1
        resolved_page_size = page_size if page_size is not None else 20
        start = (resolved_page - 1) * resolved_page_size
        end = start + resolved_page_size
        page_rows = merged[start:end]
        items = [
            _to_notification_item(row, is_broadcast=is_broadcast)
            for row, is_broadcast in page_rows
        ]
        data = build_paginated_response(
            items,
            resolved_page,
            resolved_page_size,
            total_items,
        ).model_dump(mode="json")
    else:
        items = [
            _to_notification_item(row, is_broadcast=is_broadcast)
            for row, is_broadcast in merged
        ]
        data = {"items": [item.model_dump(mode="json") for item in items]}

    return success_response(
        "Notifications fetched successfully.",
        data,
        response_cls=NotificationListResponse,
    )


async def mark_as_read(
    db: AsyncSession,
    *,
    user_id: UUID,
    notification_id: UUID,
) -> MarkNotificationReadResponse:
    notification = await get_notification_for_user(
        db,
        notification_id=notification_id,
        recipient_user_id=user_id,
    )
    if notification is not None:
        if not notification.is_read:
            notification = await persist_mark_as_read(db, notification)
            await db.commit()
            notification = await get_notification_for_user(
                db,
                notification_id=notification_id,
                recipient_user_id=user_id,
            )
        return success_response(
            "Notification marked as read.",
            _to_notification_item(notification) if notification else None,
            response_cls=MarkNotificationReadResponse,
        )

    broadcast = await get_broadcast_notification_by_id(db, notification_id)
    if broadcast is None:
        return error_response(
            "Notification not found.",
            response_cls=MarkNotificationReadResponse,
        )

    user_topics = await _user_topic_set(db, user_id)
    if not _is_broadcast_visible_to_user(broadcast, user_topics=user_topics):
        return error_response(
            "Notification not found.",
            response_cls=MarkNotificationReadResponse,
        )

    # Shared broadcast row — acknowledge read without mutating global state.
    return success_response(
        "Notification marked as read.",
        _to_notification_item(broadcast, is_broadcast=True),
        response_cls=MarkNotificationReadResponse,
    )


async def mark_all_read(
    db: AsyncSession,
    *,
    user_id: UUID,
) -> MarkAllNotificationsReadResponse:
    updated = await persist_mark_all_read(db, user_id)
    await db.commit()
    return success_response(
        "All notifications marked as read.",
        {"updated_count": updated},
        response_cls=MarkAllNotificationsReadResponse,
    )


async def create_notification(
    db: AsyncSession,
    *,
    recipient_user_id: UUID,
    notification_type: str,
    title: str,
    body: str,
    sender_user_id: UUID | None = None,
    campaign_id: UUID | None = None,
) -> Notification | None:
    """
    Common entry point for creating in-app + push notifications.

    Respects the recipient's notification preferences. Never raises for push
    delivery failures — callers (e.g. connections) should still succeed.
    """
    _ = sender_user_id
    type_name = notification_type.strip().upper()
    notification_type_row = await get_notification_type_by_name(db, type_name)
    if notification_type_row is None:
        logger.error(
            "Notification type missing type=%s recipient_user_id=%s",
            type_name,
            recipient_user_id,
        )
        return None

    preference = await _get_or_create_preferences(db, recipient_user_id)
    category_enabled = await _is_category_enabled(db, preference, type_name)

    notification: Notification | None = None
    if preference.in_app_enabled and category_enabled:
        notification = await persist_notification(
            db,
            recipient_user_id=recipient_user_id,
            notification_type_id=notification_type_row.id,
            title=title,
            body=body,
            deep_link_payload=None,
            campaign_id=campaign_id,
        )

    if preference.push_enabled and category_enabled:
        try:
            tokens = await get_active_fcm_tokens_for_users(db, [recipient_user_id])
            if tokens:
                data = {
                    "notification_type": type_name,
                    "notification_id": str(notification.id) if notification else "",
                }
                result = send_push_notifications(tokens, title, body, data)
                logger.info(
                    "Push sent type=%s recipient_user_id=%s successful=%s failed=%s",
                    type_name,
                    recipient_user_id,
                    result.get("successful_count", 0),
                    result.get("failed_count", 0),
                )
        except Exception:
            logger.exception(
                "Push notification failed type=%s recipient_user_id=%s",
                type_name,
                recipient_user_id,
            )

    try:
        await db.commit()
        if notification is not None:
            await db.refresh(notification)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to commit notification type=%s recipient_user_id=%s",
            type_name,
            recipient_user_id,
        )
        return None

    return notification
