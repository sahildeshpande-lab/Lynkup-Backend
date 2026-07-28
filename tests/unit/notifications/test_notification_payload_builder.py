from __future__ import annotations

import json
from uuid import uuid4

from apps.notifications.services.notification_payload_builder import (
    NotificationPayloadBuilder,
)


def test_build_connection_request_payload() -> None:
    notification_id = uuid4()
    sender_id = uuid4()

    payload = NotificationPayloadBuilder.build(
        notification_type="CONNECTION_REQUEST",
        notification_id=notification_id,
        sender_user_id=sender_id,
    )

    assert payload == {
        "notification_type": "CONNECTION_REQUEST",
        "notification_id": str(notification_id),
        "sender_user_id": str(sender_id),
        "deep_link": {"screen": "connections", "tab": "requests"},
    }
    assert "sender_name" not in payload
    assert "profile_photo_url" not in payload


def test_build_connection_accepted_payload() -> None:
    notification_id = uuid4()
    sender_id = uuid4()

    payload = NotificationPayloadBuilder.build(
        notification_type="CONNECTION_ACCEPTED",
        notification_id=notification_id,
        sender_user_id=sender_id,
    )

    assert payload == {
        "notification_type": "CONNECTION_ACCEPTED",
        "notification_id": str(notification_id),
        "sender_user_id": str(sender_id),
        "deep_link": {"screen": "connections", "tab": "connections"},
    }


def test_build_announcement_extends_existing_payload() -> None:
    notification_id = uuid4()
    campaign_id = uuid4()
    existing = {
        "broadcast": True,
        "campaign_id": str(campaign_id),
        "campaign_type": "ANNOUNCEMENT",
        "targets": [{"type": "MINOR", "values": ["BCA"]}],
    }

    payload = NotificationPayloadBuilder.build(
        notification_type="ANNOUNCEMENT",
        notification_id=notification_id,
        extra=existing,
    )

    assert payload["notification_type"] == "ANNOUNCEMENT"
    assert payload["notification_id"] == str(notification_id)
    assert payload["broadcast"] is True
    assert payload["campaign_id"] == str(campaign_id)
    assert payload["campaign_type"] == "ANNOUNCEMENT"
    assert payload["targets"] == [{"type": "MINOR", "values": ["BCA"]}]
    assert payload["deep_link"] == {"screen": "notifications"}
    assert "sender_user_id" not in payload


def test_for_fcm_serializes_nested_deep_link() -> None:
    payload = NotificationPayloadBuilder.build(
        notification_type="CONNECTION_REQUEST",
        notification_id=uuid4(),
        sender_user_id=uuid4(),
    )

    fcm_data = NotificationPayloadBuilder.for_fcm(payload)

    assert isinstance(fcm_data["deep_link"], str)
    assert json.loads(fcm_data["deep_link"]) == {
        "screen": "connections",
        "tab": "requests",
    }
    assert fcm_data["notification_type"] == "CONNECTION_REQUEST"
