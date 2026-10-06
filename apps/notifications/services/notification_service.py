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

IN_APP_NOTIFICATIONS_DISABLED_CODE = "IN_APP_NOTIFICATIONS_DISABLED"
IN_APP_NOTIFICATIONS_DISABLED_MESSAGE = "In-app notifications are turned off. Please turn them on to view your available notifications."

_FIREBASE_TOPICS_KEY = "firebase_topics"

# User-facing post moderation notification types (rows live in notification_types).
POST_FLAGGED = "POST_FLAGGED"
POST_REINSTATED = "POST_REINSTATED"
POST_REJECTED = "POST_REJECTED"
ACCOUNT_STATUS_CHANGED = "ACCOUNT_STATUS_CHANGED"
POST_UPDATED = "POST_UPDATED"
POST_ASSIGNED_MODERATOR = "POST_ASSIGNED_MODERATOR"
POST_ASSIGNED_SUPERADMIN = "POST_ASSIGNED_SUPERADMIN"
POST_RECOGNITION = "POST_RECOGNITION"
LEARNING_SPOTLIGHT_RECOMMENDED = "LEARNING_SPOTLIGHT_RECOMMENDED"
CONNECTION_REMINDER = "CONNECTION_REMINDER"

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
        "Your post has been deleted by our moderation team.",
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
    *,
    email_preferences: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """
    Active categories from DB as defaults (True), overlaid with the user's stored
    values. Inactive categories are excluded even if present in stored JSON.

    Always includes ``weekly_lynkup_request_reminder`` (push/in-app LynkUp digest).
    If that key is absent from stored category prefs but present on legacy
    ``email_preferences``, the email value is migrated into the category map.
    """
    from apps.notifications.email_preferences import (
        CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER,
        EXTRA_CATEGORY_PREFERENCE_DEFAULTS,
    )

    merged = dict(await get_default_category_preferences(db))
    merged.update(EXTRA_CATEGORY_PREFERENCE_DEFAULTS)
    if existing:
        for key in list(merged.keys()):
            if key in existing:
                merged[key] = _coerce_preference_bool(existing[key], default=True)
            elif key == CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER:
                upper = key.upper()
                if upper in existing:
                    merged[key] = _coerce_preference_bool(existing[upper], default=True)
    weekly_key = CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER
    stored_has_weekly = bool(
        existing
        and (weekly_key in existing or weekly_key.upper() in existing)
    )
    if (
        not stored_has_weekly
        and email_preferences
        and weekly_key in email_preferences
    ):
        merged[weekly_key] = _coerce_preference_bool(
            email_preferences[weekly_key], default=True
        )
    return merged


def _category_preference_key(notification_type: str) -> str:
    """Map notification type codes to preference keys when they differ."""
    from apps.notifications.email_preferences import (
        CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER,
    )

    if notification_type == CONNECTION_REMINDER:
        return CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER
    return notification_type


def _coerce_preference_bool(value: Any, *, default: bool = True) -> bool:
    """Normalize JSON preference values (bool/int/str) to a real bool."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def is_weekly_lynkup_reminder_enabled(
    category_preferences: dict[str, Any] | None,
    *,
    email_preferences: dict[str, Any] | None = None,
) -> bool:
    """Return whether pending LynkUp reminder push/in-app is enabled."""
    from apps.notifications.email_preferences import (
        CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER,
    )

    key = CATEGORY_PREF_WEEKLY_LYNKUP_REQUEST_REMINDER
    stored = category_preferences or {}
    if key in stored:
        return _coerce_preference_bool(stored[key], default=True)
    upper = key.upper()
    if upper in stored:
        return _coerce_preference_bool(stored[upper], default=True)
    # Legacy location before the preference moved to category_preferences.
    email_stored = email_preferences or {}
    if key in email_stored:
        return _coerce_preference_bool(email_stored[key], default=True)
    return True


async def _is_category_enabled(
    db: AsyncSession,
    preferences,
    notification_type: str,
) -> bool:
    if notification_type == CONNECTION_REMINDER:
        return is_weekly_lynkup_reminder_enabled(
            getattr(preferences, "category_preferences", None),
            email_preferences=getattr(preferences, "email_preferences", None),
        )

    categories = await _merged_category_preferences(
        db,
        preferences.category_preferences,
        email_preferences=getattr(preferences, "email_preferences", None),
    )
    return _coerce_preference_bool(
        categories.get(_category_preference_key(notification_type), True),
        default=True,
    )


def _post_id_from_payload(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    post_id = payload.get("post_id")
    if post_id:
        return str(post_id)
    deep_link = payload.get("deep_link")
    if isinstance(deep_link, dict) and deep_link.get("post_id"):
        return str(deep_link["post_id"])
    return None


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
    payload = notification.deep_link_payload
    return NotificationItem(
        id=notification.id,
        notification_type=type_name,
        notification_type_id=notification.notification_type_id,
        campaign_id=notification.campaign_id,
        title=notification.title,
        body=notification.body,
        deep_link_payload=payload,
        post_id=_post_id_from_payload(payload),
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
    # Prefer already-loaded campaign; never trigger async lazy-load here.
    campaign = notification.__dict__.get("campaign")
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
    preference: Any | None = None,
) -> list[tuple[Notification, bool, bool, Any]]:
    """Return (notification, is_broadcast, is_read, read_at) tuples newest-first."""
    if preference is None:
        preference = await _get_or_create_preferences(db, user_id)
    if not preference.in_app_enabled:
        return []

    enabled_categories = await _merged_category_preferences(
        db,
        preference.category_preferences,
        email_preferences=getattr(preference, "email_preferences", None),
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

    broadcast_campaign_ids = [
        notification.campaign_id
        for notification in broadcasts
        if notification.campaign_id is not None
    ]
    audience_by_campaign = await get_campaign_audience_for_user(
        db,
        user_id,
        broadcast_campaign_ids,
    )

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
            campaign_id = notification.campaign_id
            in_audience = bool(
                campaign_id is not None and campaign_id in audience_by_campaign
            )
            if user_topics is None:
                user_topics = await _user_topic_set(db, user_id)
            topic_match = _is_broadcast_visible_to_user(
                notification,
                user_topics=user_topics,
                user_registered_at=user_registered_at,
            )
            if not (in_audience or topic_match):
                continue
        elif type_name != "ANNOUNCEMENT":
            continue
        visible_broadcasts.append(notification)

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


def _notification_list_reason(*, in_app_enabled: bool) -> dict[str, str]:
    if in_app_enabled:
        return {}
    return {
        "code": IN_APP_NOTIFICATIONS_DISABLED_CODE,
        "message": IN_APP_NOTIFICATIONS_DISABLED_MESSAGE,
    }


def _notification_type_enabled(
    notification: Notification,
    enabled_categories: dict[str, bool],
) -> bool:
    type_name = None
    if getattr(notification, "notification_type", None) is not None:
        type_name = notification.notification_type.name
    if not type_name:
        return True
    return bool(enabled_categories.get(_category_preference_key(type_name), True))


async def _get_or_create_preferences(db: AsyncSession, user_id: UUID):
    from apps.notifications.email_preferences import (
        EXTRA_CATEGORY_PREFERENCE_DEFAULTS,
        merge_email_preferences,
    )

    preference = await get_preferences_by_user_id(db, user_id)
    if preference is not None:
        # Ensure newly added active categories appear; drop deactivated from the view.
        merged = await _merged_category_preferences(
            db,
            preference.category_preferences,
            email_preferences=preference.email_preferences,
        )
        merged_email = merge_email_preferences(preference.email_preferences)
        needs_update = merged != (preference.category_preferences or {}) or merged_email != (
            preference.email_preferences or {}
        )
        if needs_update:
            preference = await persist_update_preferences(
                db,
                preference,
                category_preferences=merged,
                email_preferences=merged_email,
            )
        return preference

    defaults = await get_default_category_preferences(db)
    defaults = {**defaults, **EXTRA_CATEGORY_PREFERENCE_DEFAULTS}
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
    from apps.notifications.email_preferences import merge_email_preferences

    preference = await _get_or_create_preferences(db, user_id)
    await db.commit()
    return success_response(
        "Notification preferences fetched successfully.",
        NotificationPreferencesData(
            push_enabled=preference.push_enabled,
            in_app_enabled=preference.in_app_enabled,
            email_preferences=merge_email_preferences(preference.email_preferences),
            category_preferences=await _merged_category_preferences(
                db,
                preference.category_preferences,
                email_preferences=preference.email_preferences,
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
    from apps.notifications.email_preferences import (
        EXTRA_CATEGORY_PREFERENCE_DEFAULTS,
        merge_email_preferences,
    )

    preference = await _get_or_create_preferences(db, user_id)

    merged_categories = None
    if payload.category_preferences is not None:
        merged_categories = await _merged_category_preferences(
            db,
            preference.category_preferences,
            email_preferences=preference.email_preferences,
        )
        extra_keys = set(EXTRA_CATEGORY_PREFERENCE_DEFAULTS)
        for key, value in payload.category_preferences.items():
            key_raw = str(key).strip()
            key_lower = key_raw.lower()
            if key_lower in extra_keys:
                merged_categories[key_lower] = bool(value)
                continue
            key_str = key_raw.upper()
            if key_str in merged_categories:
                merged_categories[key_str] = bool(value)

    merged_email = None
    if payload.email_preferences is not None:
        merged_email = merge_email_preferences(preference.email_preferences)
        for key, value in payload.email_preferences.items():
            merged_email[key] = bool(value)

    preference = await persist_update_preferences(
        db,
        preference,
        push_enabled=payload.push_enabled,
        in_app_enabled=payload.in_app_enabled,
        category_preferences=merged_categories,
        email_preferences=merged_email,
    )
    await db.commit()
    await db.refresh(preference)

    return success_response(
        "Notification preferences updated successfully.",
        NotificationPreferencesData(
            push_enabled=preference.push_enabled,
            in_app_enabled=preference.in_app_enabled,
            email_preferences=merge_email_preferences(preference.email_preferences),
            category_preferences=await _merged_category_preferences(
                db,
                preference.category_preferences,
                email_preferences=preference.email_preferences,
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
    preference = await _get_or_create_preferences(db, user_id)
    reason = _notification_list_reason(in_app_enabled=preference.in_app_enabled)
    merged = await _list_unified_notifications_for_user(
        db,
        user_id,
        is_read=is_read,
        preference=preference,
    )
    # Persist any preference create/merge done while building the inbox.
    await db.commit()
    total_items = len(merged)

    if is_read is None:
        total_unread = sum(1 for _, _, is_read_value, _ in merged if not is_read_value)
    elif is_read is False:
        total_unread = total_items
    else:
        all_unread = await _list_unified_notifications_for_user(
            db,
            user_id,
            is_read=False,
            preference=preference,
        )
        total_unread = len(all_unread)

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
        paginated_dict = build_paginated_response(
            items,
            resolved_page,
            resolved_page_size,
            total_items,
        ).model_dump(mode="json")
        data = {
            "items": paginated_dict["items"],
            "Totalcount": total_unread,
            "page": paginated_dict["page"],
            "pageSize": paginated_dict["pageSize"],
            "totalItems": paginated_dict["totalItems"],
            "totalPages": paginated_dict["totalPages"],
            "reason": reason,
        }
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
        data = {
            "items": [item.model_dump(mode="json") for item in items],
            "Totalcount": total_unread,
            "reason": reason,
        }

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

    # Match list visibility: ANNOUNCEMENT always (post-registration);
    # TOPIC when user is in campaign audience or has a Firebase topic match.
    user_registered_at = await _user_registered_at(db, user_id)
    if not _is_broadcast_after_registration(
        broadcast,
        user_registered_at=user_registered_at,
    ):
        return error_response(
            "Notification not found.",
            response_cls=MarkNotificationReadResponse,
        )

    type_name = (
        broadcast.notification_type.name
        if getattr(broadcast, "notification_type", None) is not None
        else None
    )
    if type_name == "TOPIC":
        campaign_ids = (
            [broadcast.campaign_id] if broadcast.campaign_id is not None else []
        )
        audience_by_campaign = await get_campaign_audience_for_user(
            db,
            user_id,
            campaign_ids,
        )
        in_audience = bool(
            broadcast.campaign_id is not None
            and broadcast.campaign_id in audience_by_campaign
        )
        user_topics = await _user_topic_set(db, user_id)
        topic_match = _is_broadcast_visible_to_user(
            broadcast,
            user_topics=user_topics,
            user_registered_at=user_registered_at,
        )
        if not (in_audience or topic_match):
            return error_response(
                "Notification not found.",
                response_cls=MarkNotificationReadResponse,
            )
    elif type_name != "ANNOUNCEMENT":
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
    send_push: bool = True,
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

    if type_name == CONNECTION_REMINDER and not category_enabled:
        logger.info(
            "LynkUp reminder skipped recipient_user_id=%s "
            "reason=weekly_lynkup_request_reminder_disabled",
            recipient_user_id,
        )
        return None

    notification: Notification | None = None
    data_payload: dict[str, Any] = NotificationPayloadBuilder.build(
        notification_type=type_name,
        notification_id=None,
        sender_user_id=sender_user_id,
        extra=extra,
    )

    # CONNECTION_REMINDER is gated by weekly_lynkup_request_reminder (category_enabled).
    # Always persist an in-app row when that preference is on so the reminder is
    # visible and the producer can confirm delivery. Other types still honor
    # in_app_enabled.
    should_persist_in_app = category_enabled and (
        preference.in_app_enabled or type_name == CONNECTION_REMINDER
    )
    if should_persist_in_app:
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

    # Push requires global push + category. For CONNECTION_REMINDER the category
    # key is weekly_lynkup_request_reminder (off => no push delivery).
    if send_push and preference.push_enabled and category_enabled:
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
    elif not send_push:
        logger.info(
            "Push notification skipped type=%s recipient_user_id=%s reason=admin_push_disabled",
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
    """Get or create a notification_types row (data only — no schema change).

    Uses flush (not commit) so callers that own the transaction — e.g. the
    connection-reminder producer — are not disrupted by a nested commit.
    """
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
        await db.flush()
        await db.refresh(row)
        logger.info("Seeded notification type name=%s", name)
        return row
    except Exception:
        # Concurrent insert or transient failure — re-read without rolling back
        # the caller's broader transaction when possible.
        try:
            await db.refresh(row)
            if getattr(row, "id", None) is not None:
                return row
        except Exception:
            pass
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


async def notify_post_recognition(
    db: AsyncSession,
    *,
    author_user_id: UUID,
    post_id: UUID,
    milestone: int,
    first_name: str | None = None,
) -> Notification | None:
    """Notify a post author that their post reached a like milestone."""
    if milestone <= 0:
        return None

    greeting_name = (first_name or "").strip()
    if greeting_name:
        body = f"{greeting_name}, your post is getting recognized! Keep it up! 🔥"
    else:
        body = "Your post is getting recognized! Keep it up! 🔥"
    title = "Post Recognition"

    try:
        ensured = await _ensure_notification_type(
            db,
            POST_RECOGNITION,
            description="Post reached a configured like milestone",
        )
        if ensured is None:
            return None

        return await create_notification(
            db,
            recipient_user_id=author_user_id,
            notification_type=POST_RECOGNITION,
            title=title,
            body=body,
            extra={
                "post_id": str(post_id),
                "milestone": milestone,
            },
        )
    except Exception:
        logger.exception(
            "Failed to notify post recognition author_user_id=%s post_id=%s milestone=%s",
            author_user_id,
            post_id,
            milestone,
        )
        return None


def _learning_spotlight_recommended_body(greeting_name: str | None) -> str:
    name = (greeting_name or "").strip()
    if name:
        return f"{name}, your Learning Spotlight articles are ready. Happy Learning 😊"
    return "Learning Spotlight articles are ready. Happy Learning 😊"


async def notify_learning_spotlight_recommended(
    db: AsyncSession,
    *,
    user_id: UUID,
    spotlight_type: object,
    cycle_day: int,
    first_name: str | None = None,
    send_push: bool = True,
) -> Notification | None:
    """Notify a user that a Learning Spotlight recommendation is ready."""
    if cycle_day <= 0:
        return None

    type_value = (
        spotlight_type.value
        if hasattr(spotlight_type, "value")
        else str(spotlight_type or "").strip()
    )
    greeting_name = (first_name or "").strip().title()
    body = _learning_spotlight_recommended_body(greeting_name)
    title = "Learning Spotlight"

    try:
        if first_name is None:
            from apps.profiles.db_models import Profile

            profile_first_name = (
                await db.execute(select(Profile.first_name).where(Profile.user_id == user_id))
            ).scalar_one_or_none()
            if isinstance(profile_first_name, str) and profile_first_name.strip():
                greeting_name = profile_first_name.strip()
                body = _learning_spotlight_recommended_body(greeting_name)

        ensured = await _ensure_notification_type(
            db,
            LEARNING_SPOTLIGHT_RECOMMENDED,
            description="Learning Spotlight recommendation generated for user",
        )
        if ensured is None:
            return None

        return await create_notification(
            db,
            recipient_user_id=user_id,
            notification_type=LEARNING_SPOTLIGHT_RECOMMENDED,
            title=title,
            body=body,
            extra={
                "spotlight_type": type_value,
                "cycle_day": cycle_day,
            },
            send_push=send_push,
        )
    except Exception:
        logger.exception(
            "Failed to notify learning spotlight recommendation user_id=%s cycle_day=%s",
            user_id,
            cycle_day,
        )
        return None


async def notify_connection_reminder(
    db: AsyncSession,
    *,
    recipient_user_id: UUID,
    pending_count: int,
    sender_name: str | None = None,
    sender_user_id: UUID | None = None,
    send_push: bool = True,
) -> Notification | None:
    """Notify a user that they have pending connection (LynkUp) requests."""
    if pending_count <= 0:
        return None

    title = "LynkUp Reminder"
    if pending_count == 1 and sender_name and sender_name.strip():
        body = f"You have a pending LynkUp request from {sender_name.strip()}."
    elif pending_count == 1:
        body = "You have a pending LynkUp request waiting for your response."
    else:
        body = f"You have {pending_count} pending LynkUp requests waiting for your response."

    try:
        ensured = await _ensure_notification_type(
            db,
            CONNECTION_REMINDER,
            description="Weekly reminder for pending connection requests",
        )
        if ensured is None:
            return None

        return await create_notification(
            db,
            recipient_user_id=recipient_user_id,
            notification_type=CONNECTION_REMINDER,
            title=title,
            body=body,
            sender_user_id=sender_user_id,
            extra={"pending_count": pending_count},
            send_push=send_push,
        )
    except Exception:
        logger.exception(
            "Failed to notify connection reminder recipient_user_id=%s",
            recipient_user_id,
        )
        return None


async def _resolve_user_full_name(db: AsyncSession, user_id: UUID) -> str:
    from apps.accounts.db_models import User
    from apps.profiles.db_models import Profile

    stmt = (
        select(User, Profile)
        .outerjoin(Profile, Profile.user_id == User.id)
        .where(User.id == user_id)
    )
    row = (await db.execute(stmt)).first()
    if row is not None:
        user, profile = row
        if profile is not None:
            parts = [
                p.strip()
                for p in (getattr(profile, "first_name", None), getattr(profile, "last_name", None))
                if p and p.strip()
            ]
            if parts:
                return " ".join(parts)
            if getattr(profile, "username", None) and profile.username.strip():
                return profile.username.strip()
        if user is not None and getattr(user, "email", None):
            return user.email
    return "User"


async def _fetch_superadmin_user_ids(db: AsyncSession) -> list[UUID]:
    from apps.accounts.db_models import Role, User, UserRole

    stmt = (
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.name == "superadmin", User.is_deleted.is_(False))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def notify_post_assigned(
    db: AsyncSession,
    *,
    post_id: UUID,
    author_user_id: UUID,
    moderator_id: UUID,
) -> None:
    """
    Notify the assigned moderator and superadmins when a post is assigned for review.
    """
    try:
        author_name = await _resolve_user_full_name(db, author_user_id)
        moderator_name = await _resolve_user_full_name(db, moderator_id)

        # 1. Notify assigned moderator
        await _ensure_notification_type(
            db,
            POST_ASSIGNED_MODERATOR,
            description="Post assigned to moderator for review",
        )
        mod_title = "New Post Assigned - Moderator"
        mod_body = f"{author_name} created a new post and it has been assigned to you for moderation."
        await create_notification(
            db,
            recipient_user_id=moderator_id,
            notification_type=POST_ASSIGNED_MODERATOR,
            title=mod_title,
            body=mod_body,
            sender_user_id=author_user_id,
            extra={
                "post_id": str(post_id),
                "author_user_id": str(author_user_id),
                "moderator_id": str(moderator_id),
            },
        )

        # 2. Notify all superadmins
        await _ensure_notification_type(
            db,
            POST_ASSIGNED_SUPERADMIN,
            description="Post assigned to moderator (superadmin notification)",
        )
        superadmin_ids = await _fetch_superadmin_user_ids(db)
        sa_title = "New Post Assigned - Superadmin"
        sa_body = f"{author_name} created a new post and it has been assigned to {moderator_name} for moderation."
        for sa_id in superadmin_ids:
            if sa_id == moderator_id:
                continue
            await create_notification(
                db,
                recipient_user_id=sa_id,
                notification_type=POST_ASSIGNED_SUPERADMIN,
                title=sa_title,
                body=sa_body,
                sender_user_id=author_user_id,
                extra={
                    "post_id": str(post_id),
                    "author_user_id": str(author_user_id),
                    "moderator_id": str(moderator_id),
                },
            )

        # 3. Record in admin_activity_logs (for /admin/notification and /admin/activity-logs)
        from apps.administration.repositories.admin_activity_log_repository import (
            create_admin_activity_log_record,
        )

        log_user_id = superadmin_ids[0] if superadmin_ids else author_user_id
        await create_admin_activity_log_record(
            db,
            user_id=log_user_id,
            role="superadmin",
            action="assign",
            module="post",
            record_id=post_id,
            description=sa_body,
            metadata={
                "post_id": str(post_id),
                "author_user_id": str(author_user_id),
                "moderator_id": str(moderator_id),
            },
        )
        await db.commit()
    except Exception:
        logger.exception(
            "Failed to send post assigned notifications post_id=%s author_user_id=%s moderator_id=%s",
            post_id,
            author_user_id,
            moderator_id,
        )


async def notify_post_edited(
    db: AsyncSession,
    *,
    post_id: UUID,
    author_user_id: UUID,
    moderator_id: UUID | None = None,
) -> None:
    """
    Notify superadmins and the assigned moderator when a user updates/edits their post.
    """
    try:
        author_name = await _resolve_user_full_name(db, author_user_id)
        await _ensure_notification_type(
            db,
            POST_UPDATED,
            description="User updated a post in review",
        )
        title = "Post Updated"
        body = f"{author_name} Updated the post"

        recipients: set[UUID] = set()
        if moderator_id is not None:
            recipients.add(moderator_id)
        superadmin_ids = await _fetch_superadmin_user_ids(db)
        recipients.update(superadmin_ids)

        for recipient_id in recipients:
            await create_notification(
                db,
                recipient_user_id=recipient_id,
                notification_type=POST_UPDATED,
                title=title,
                body=body,
                sender_user_id=author_user_id,
                extra={
                    "post_id": str(post_id),
                    "author_user_id": str(author_user_id),
                },
            )

        # Record in admin_activity_logs (for /admin/notification and /admin/activity-logs)
        from apps.administration.repositories.admin_activity_log_repository import (
            create_admin_activity_log_record,
        )

        log_user_id = superadmin_ids[0] if superadmin_ids else author_user_id
        await create_admin_activity_log_record(
            db,
            user_id=log_user_id,
            role="superadmin",
            action="update",
            module="post",
            record_id=post_id,
            description=body,
            metadata={
                "post_id": str(post_id),
                "author_user_id": str(author_user_id),
                "moderator_id": str(moderator_id) if moderator_id else None,
            },
        )
        await db.commit()
    except Exception:
        logger.exception(
            "Failed to send post updated notifications post_id=%s author_user_id=%s",
            post_id,
            author_user_id,
        )
