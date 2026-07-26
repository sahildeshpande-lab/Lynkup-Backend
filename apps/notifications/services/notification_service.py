from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.notifications.db_models import Notification
from apps.notifications.repositories.campaign_audience_repository import (
    get_active_fcm_tokens_for_users,
)
from apps.notifications.repositories.notification_repository import (
    DEFAULT_CATEGORY_PREFERENCES,
    count_notifications_for_user,
    create_notification as persist_notification,
    create_preferences,
    get_notification_for_user,
    get_notification_type_by_name,
    get_preferences_by_user_id,
    list_notifications_for_user,
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
from common.pagination import build_paginated_response
from common.responses import error_response, success_response
from core.auth.services import send_push_notifications

logger = logging.getLogger(__name__)

PREFERENCES_FETCHED_MESSAGE = "Notification preferences fetched successfully."
PREFERENCES_UPDATED_MESSAGE = "Notification preferences updated successfully."
NOTIFICATIONS_FETCHED_MESSAGE = "Notifications fetched successfully."
NOTIFICATION_READ_MESSAGE = "Notification marked as read."
NOTIFICATIONS_READ_ALL_MESSAGE = "All notifications marked as read."
NOTIFICATION_NOT_FOUND_MESSAGE = "Notification not found."
NOTIFICATION_TYPE_MISSING_MESSAGE = "Notification type is not configured."


def _merged_category_preferences(
    existing: dict[str, Any] | None,
) -> dict[str, bool]:
    merged = dict(DEFAULT_CATEGORY_PREFERENCES)
    if existing:
        for key, value in existing.items():
            merged[str(key)] = bool(value)
    return merged


def _is_category_enabled(preferences, notification_type: str) -> bool:
    categories = _merged_category_preferences(preferences.category_preferences)
    return bool(categories.get(notification_type, True))


def _to_notification_item(notification: Notification) -> NotificationItem:
    type_name = None
    if getattr(notification, "notification_type", None) is not None:
        type_name = notification.notification_type.name
    return NotificationItem(
        id=notification.id,
        notification_type=type_name,
        notification_type_id=notification.notification_type_id,
        campaign_id=notification.campaign_id,
        title=notification.title,
        body=notification.body,
        deep_link_payload=notification.deep_link_payload,
        is_read=notification.is_read,
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


async def _get_or_create_preferences(db: AsyncSession, user_id: UUID):
    preference = await get_preferences_by_user_id(db, user_id)
    if preference is not None:
        # Ensure defaults exist for any newly introduced categories.
        merged = _merged_category_preferences(preference.category_preferences)
        if merged != (preference.category_preferences or {}):
            preference = await persist_update_preferences(
                db,
                preference,
                category_preferences=merged,
            )
        return preference
    return await create_preferences(
        db,
        user_id=user_id,
        category_preferences=dict(DEFAULT_CATEGORY_PREFERENCES),
    )


async def get_preferences(
    db: AsyncSession,
    *,
    user_id: UUID,
) -> NotificationPreferencesResponse:
    preference = await _get_or_create_preferences(db, user_id)
    await db.commit()
    return success_response(
        PREFERENCES_FETCHED_MESSAGE,
        NotificationPreferencesData(
            push_enabled=preference.push_enabled,
            in_app_enabled=preference.in_app_enabled,
            category_preferences=_merged_category_preferences(
                preference.category_preferences
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
        merged_categories = _merged_category_preferences(preference.category_preferences)
        for key, value in payload.category_preferences.items():
            merged_categories[str(key)] = bool(value)

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
        PREFERENCES_UPDATED_MESSAGE,
        NotificationPreferencesData(
            push_enabled=preference.push_enabled,
            in_app_enabled=preference.in_app_enabled,
            category_preferences=_merged_category_preferences(
                preference.category_preferences
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
    unread_only: bool = False,
) -> NotificationListResponse:
    paginate = page is not None or page_size is not None
    if paginate:
        resolved_page = page if page is not None else 1
        resolved_page_size = page_size if page_size is not None else 20
        total_items = await count_notifications_for_user(
            db,
            user_id,
            unread_only=unread_only,
        )
        rows = await list_notifications_for_user(
            db,
            user_id,
            unread_only=unread_only,
            page=resolved_page,
            page_size=resolved_page_size,
        )
        items = [_to_notification_item(row) for row in rows]
        data = build_paginated_response(
            items,
            resolved_page,
            resolved_page_size,
            total_items,
        ).model_dump(mode="json")
    else:
        rows = await list_notifications_for_user(
            db,
            user_id,
            unread_only=unread_only,
        )
        items = [_to_notification_item(row) for row in rows]
        data = {"items": [item.model_dump(mode="json") for item in items]}

    return success_response(
        NOTIFICATIONS_FETCHED_MESSAGE,
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
    if notification is None:
        return error_response(
            NOTIFICATION_NOT_FOUND_MESSAGE,
            response_cls=MarkNotificationReadResponse,
        )

    if not notification.is_read:
        notification = await persist_mark_as_read(db, notification)
        await db.commit()
        notification = await get_notification_for_user(
            db,
            notification_id=notification_id,
            recipient_user_id=user_id,
        )

    return success_response(
        NOTIFICATION_READ_MESSAGE,
        _to_notification_item(notification) if notification else None,
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
        NOTIFICATIONS_READ_ALL_MESSAGE,
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
    category_enabled = _is_category_enabled(preference, type_name)

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
