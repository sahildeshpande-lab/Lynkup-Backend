from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.notifications.db_models import Notification, NotificationType
from apps.notifications.services import notification_service as ns
from apps.notifications.services.notification_payload_builder import (
    NotificationPayloadBuilder,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("notification_type", "title"),
    [
        (ns.POST_FLAGGED, "Post Flagged"),
        (ns.POST_REJECTED, "Post Rejected"),
        (ns.POST_REINSTATED, "Post Reinstated"),
    ],
)
async def test_notify_post_author_passes_post_id(notification_type, title):
    db = AsyncMock()
    post_id = uuid4()
    author_id = uuid4()

    with (
        patch.object(ns, "_ensure_notification_type", AsyncMock(return_value=object())),
        patch.object(ns, "create_notification", AsyncMock(return_value=object())) as create,
    ):
        await ns.notify_post_author(
            db,
            post_id=post_id,
            author_user_id=author_id,
            notification_type=notification_type,
        )

    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["recipient_user_id"] == author_id
    assert kwargs["notification_type"] == notification_type
    assert kwargs["title"] == title
    assert kwargs["extra"] == {"post_id": str(post_id)}


def test_moderation_notification_item_includes_post_id():
    post_id = uuid4()
    notification_id = uuid4()
    type_id = uuid4()
    payload = NotificationPayloadBuilder.build(
        notification_type=ns.POST_FLAGGED,
        notification_id=notification_id,
        extra={"post_id": str(post_id)},
    )
    notif_type = NotificationType(id=type_id, name=ns.POST_FLAGGED, is_active=True)
    notification = Notification(
        id=notification_id,
        recipient_user_id=uuid4(),
        notification_type_id=type_id,
        title="Post Flagged",
        body="Your post has been flagged by our moderation team and is currently under review.",
        deep_link_payload=payload,
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )
    notification.notification_type = notif_type

    item = ns._to_notification_item(notification)

    assert item.post_id == str(post_id)
    assert item.deep_link_payload["post_id"] == str(post_id)
    assert item.deep_link_payload["deep_link"] == {
        "screen": "post",
        "post_id": str(post_id),
    }
    fcm_data = NotificationPayloadBuilder.for_fcm(payload)
    assert fcm_data["post_id"] == str(post_id)
