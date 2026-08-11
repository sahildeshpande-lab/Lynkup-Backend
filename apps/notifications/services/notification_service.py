from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.notifications.db_models import Notification
from apps.notifications.repositories.campaign_audience_repository import (
    get_active_push_targets_for_users,
    get_campaign_audience_for_user,
    mark_all_campaign_audience_read,
    mark_campaign_audience_read,
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
from apps.notifications.services.notification_payload_builder import (
    NotificationPayloadBuilder,
)
from common.pagination import build_paginated_response
from common.responses import error_response, success_response
from core.push import send_push_to_devices

logger = logging.getLogger(__name__)

_FIREBASE_TOPICS_KEY = "firebase_topics"

# User-facing post moderation notification types (rows live in notification_types).
POST_FLAGGED = "POST_FLAGGED"
POST_REINSTATED = "POST_REINSTATED"
POST_REJECTED = "POST_REJECTED"
ACCOUNT_STATUS_CHANGED = "ACCOUNT_STATUS_CHANGED"

_POST_AUTHOR_NOTIFICATION_COPY: dict[str, tuple[str, str]] = {
    POST_FLAGGED: (
        "Post Flagged",
        "Your post has been flagged by our moderation team and is currently under review.",
    ),
    POST_REINSTATED: (
        "Post Reinstated",
        "Your post has been reinstated and is now visible to other users.",
    ),
    POST_REJECTED: (
        "Post Rejected",
        "Your post has been rejected by our moderation team.",
    ),
}

_POST_AUTHOR_TYPE_DESCRIPTIONS: dict[str, str] = {
    POST_FLAGGED: "Post flagged by moderation",
    POST_REINSTATED: "Post reinstated by moderation",
    POST_REJECTED: "Post rejected by moderation",
}


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
    broadcast_is_read: bool | None = None,
    broadcast_read_at=None,
) -> NotificationItem:
    type_name = None
    if getattr(notification, "notification_type", None) is not None:
        type_name = notification.notification_type.name
    if is_broadcast:
        is_read = bool(broadcast_is_read)
        read_at = broadcast_read_at
    else:
        is_read = notification.is_read
        read_at = notification.read_at
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


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _user_registered_at(db: AsyncSession, user_id: UUID) -> datetime | None:
    """Return the user's account creation time (UTC), if known."""
    from apps.accounts.db_models import User

    created_at = (
        await db.execute(select(User.created_at).where(User.id == user_id))
    ).scalar_one_or_none()
    return _as_aware_utc(created_at)


def _broadcast_sent_at(notification: Notification) -> datetime | None:
    """Effective send time for a broadcast (campaign.sent_at when present)."""
    campaign = getattr(notification, "campaign", None)
    sent_at = getattr(campaign, "sent_at", None) if campaign is not None else None
    return _as_aware_utc(sent_at) or _as_aware_utc(
        getattr(notification, "created_at", None)
    )


def _is_broadcast_after_registration(
    notification: Notification,
    *,
    user_registered_at: datetime | None,
) -> bool:
    """
    New users must not see globals dispatched before their account existed.

    When registration time is unknown, keep prior visibility behavior.
    """
    if user_registered_at is None:
        return True
    sent_at = _broadcast_sent_at(notification)
    if sent_at is None:
        return True
    return sent_at >= user_registered_at


def _is_broadcast_visible_to_user(
    notification: Notification,
    *,
    user_topics: set[str],
    user_registered_at: datetime | None = None,
) -> bool:
    if not _is_broadcast_after_registration(
        notification,
        user_registered_at=user_registered_at,
    ):
        return False
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
) -> list[tuple[Notification, bool, bool, Any]]:
    """Return (notification, is_broadcast, is_read, read_at) tuples newest-first."""
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

    broadcasts = await list_broadcast_notifications(db)
    user_registered_at = await _user_registered_at(db, user_id)

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
        if not _is_broadcast_after_registration(
            notification,
            user_registered_at=user_registered_at,
        ):
            continue
        if type_name == "TOPIC":
            if user_topics is None:
                user_topics = await _user_topic_set(db, user_id)
            if not _is_broadcast_visible_to_user(
                notification,
                user_topics=user_topics,
                user_registered_at=user_registered_at,
            ):
                continue
        elif type_name != "ANNOUNCEMENT":
            continue
        visible_broadcasts.append(notification)

    audience_by_campaign = await get_campaign_audience_for_user(
        db,
        user_id,
        [notification.campaign_id for notification in visible_broadcasts if notification.campaign_id],
    )

    merged: list[tuple[Notification, bool, bool, Any]] = [
        (row, False, row.is_read, row.read_at) for row in personal
    ]
    for notification in visible_broadcasts:
        campaign_id = notification.campaign_id
        audience = audience_by_campaign.get(campaign_id) if campaign_id else None
        broadcast_is_read = audience.is_read if audience is not None else False
        broadcast_read_at = audience.read_at if audience is not None else None
        if is_read is True and not broadcast_is_read:
            continue
        if is_read is False and broadcast_is_read:
            continue
        merged.append(
            (notification, True, broadcast_is_read, broadcast_read_at),
        )

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
            _to_notification_item(
                row,
                is_broadcast=is_broadcast,
                broadcast_is_read=is_read_value if is_broadcast else None,
                broadcast_read_at=read_at_value if is_broadcast else None,
            )
            for row, is_broadcast, is_read_value, read_at_value in page_rows
        ]
        data = build_paginated_response(
            items,
            resolved_page,
            resolved_page_size,
            total_items,
        ).model_dump(mode="json")
    else:
        items = [
            _to_notification_item(
                row,
                is_broadcast=is_broadcast,
                broadcast_is_read=is_read_value if is_broadcast else None,
                broadcast_read_at=read_at_value if is_broadcast else None,
            )
            for row, is_broadcast, is_read_value, read_at_value in merged
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

    user_registered_at = await _user_registered_at(db, user_id)
    user_topics = await _user_topic_set(db, user_id)
    if not _is_broadcast_visible_to_user(
        broadcast,
        user_topics=user_topics,
        user_registered_at=user_registered_at,
    ):
        return error_response(
            "Notification not found.",
            response_cls=MarkNotificationReadResponse,
        )

    if broadcast.campaign_id is None:
        return error_response(
            "Notification not found.",
            response_cls=MarkNotificationReadResponse,
        )

    audience = await mark_campaign_audience_read(
        db,
        user_id=user_id,
        campaign_id=broadcast.campaign_id,
    )
    await db.commit()

    return success_response(
        "Notification marked as read.",
        _to_notification_item(
            broadcast,
            is_broadcast=True,
            broadcast_is_read=audience.is_read,
            broadcast_read_at=audience.read_at,
        ),
        response_cls=MarkNotificationReadResponse,
    )


async def mark_all_read(
    db: AsyncSession,
    *,
    user_id: UUID,
) -> MarkAllNotificationsReadResponse:
    updated = await persist_mark_all_read(db, user_id)
    visible_broadcasts = await _list_unified_notifications_for_user(
        db,
        user_id,
        is_read=None,
    )
    campaign_ids = [
        notification.campaign_id
        for notification, is_broadcast, is_read_value, _read_at in visible_broadcasts
        if is_broadcast and notification.campaign_id is not None and not is_read_value
    ]
    updated += await mark_all_campaign_audience_read(
        db,
        user_id=user_id,
        campaign_ids=campaign_ids,
    )
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
    extra: dict[str, Any] | None = None,
) -> Notification | None:
    """
    Common entry point for creating in-app + push notifications.

    Respects the recipient's notification preferences. Never raises for push
    delivery failures — callers (e.g. connections) should still succeed.
    """
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
    logger.info(
        "Notification dispatch prepared type=%s recipient_user_id=%s sender_user_id=%s "
        "push_enabled=%s in_app_enabled=%s category_enabled=%s",
        type_name,
        recipient_user_id,
        sender_user_id,
        preference.push_enabled,
        preference.in_app_enabled,
        category_enabled,
    )

    notification: Notification | None = None
    data_payload: dict[str, Any] = NotificationPayloadBuilder.build(
        notification_type=type_name,
        notification_id=None,
        sender_user_id=sender_user_id,
        extra=extra,
    )

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
        data_payload = NotificationPayloadBuilder.build(
            notification_type=type_name,
            notification_id=notification.id,
            sender_user_id=sender_user_id,
            extra=extra,
        )
        notification.deep_link_payload = data_payload
        db.add(notification)
        await db.flush()

    if preference.push_enabled and category_enabled:
        try:
            targets = await get_active_push_targets_for_users(db, [recipient_user_id])
            logger.info(
                "Push notification token lookup type=%s recipient_user_id=%s target_count=%s",
                type_name,
                recipient_user_id,
                len(targets),
            )
            if targets:
                result = await send_push_to_devices(
                    targets,
                    title,
                    body,
                    NotificationPayloadBuilder.for_fcm(data_payload),
                )
                logger.info(
                    "Push sent type=%s recipient_user_id=%s successful=%s failed=%s",
                    type_name,
                    recipient_user_id,
                    result.get("successful_count", 0),
                    result.get("failed_count", 0),
                )
            else:
                logger.warning(
                    "Push notification skipped type=%s recipient_user_id=%s reason=no_active_push_targets",
                    type_name,
                    recipient_user_id,
                )
        except Exception:
            logger.exception(
                "Push notification failed type=%s recipient_user_id=%s",
                type_name,
                recipient_user_id,
            )
    else:
        logger.info(
            "Push notification skipped type=%s recipient_user_id=%s push_enabled=%s category_enabled=%s",
            type_name,
            recipient_user_id,
            preference.push_enabled,
            category_enabled,
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


async def _ensure_notification_type(
    db: AsyncSession,
    name: str,
    *,
    description: str | None = None,
) -> Any:
    """Get or create a notification_types row (data only — no schema change)."""
    from apps.notifications.db_models import NotificationType
    from common.time import utc_now

    existing = await get_notification_type_by_name(db, name)
    if existing is not None:
        return existing

    row = NotificationType(
        name=name,
        description=description,
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(row)
    try:
        await db.commit()
        await db.refresh(row)
        logger.info("Seeded notification type name=%s", name)
        return row
    except Exception:
        await db.rollback()
        # Concurrent insert — re-read.
        existing = await get_notification_type_by_name(db, name)
        if existing is not None:
            return existing
        logger.exception("Failed to seed notification type name=%s", name)
        return None


async def notify_post_author(
    db: AsyncSession,
    *,
    post_id: UUID,
    author_user_id: UUID,
    notification_type: str,
) -> Notification | None:
    """
    Notify a post author about a moderation (or other post) event.

    Reuses ``create_notification`` for in-app + platform-aware push. Never raises —
    callers must treat moderation as successful even when this returns None.
    """
    type_name = (notification_type or "").strip().upper()
    copy = _POST_AUTHOR_NOTIFICATION_COPY.get(type_name)
    if copy is None:
        logger.error(
            "Unsupported post-author notification type=%s post_id=%s author_user_id=%s",
            type_name,
            post_id,
            author_user_id,
        )
        return None

    title, body = copy
    try:
        ensured = await _ensure_notification_type(
            db,
            type_name,
            description=_POST_AUTHOR_TYPE_DESCRIPTIONS.get(type_name),
        )
        if ensured is None:
            return None

        return await create_notification(
            db,
            recipient_user_id=author_user_id,
            notification_type=type_name,
            title=title,
            body=body,
            extra={"post_id": str(post_id)},
        )
    except Exception:
        logger.exception(
            "Failed to notify post author type=%s post_id=%s author_user_id=%s",
            type_name,
            post_id,
            author_user_id,
        )
        return None


async def notify_account_status(
    db: AsyncSession,
    *,
    user_id: UUID,
    status: str,
    reason: str | None = None,
    sender_user_id: UUID | None = None,
) -> Notification | None:
    """
    Notify a user that their account status changed.

    Title: ``Your account is {status}``
    Body: the moderator reason (falls back to a short default).

    Never raises — status updates must succeed even when notification fails.
    """
    status_value = (status or "").strip().lower()
    if not status_value:
        return None

    title = f"Your account is {status_value}"
    body = (reason or "").strip() or f"Your account status was updated to {status_value}."

    try:
        ensured = await _ensure_notification_type(
            db,
            ACCOUNT_STATUS_CHANGED,
            description="Account status changed by moderation",
        )
        if ensured is None:
            return None

        return await create_notification(
            db,
            recipient_user_id=user_id,
            notification_type=ACCOUNT_STATUS_CHANGED,
            title=title,
            body=body,
            sender_user_id=sender_user_id,
            extra={
                "status": status_value,
                "reason": body,
            },
        )
    except Exception:
        logger.exception(
            "Failed to notify account status user_id=%s status=%s",
            user_id,
            status_value,
        )
        return None
