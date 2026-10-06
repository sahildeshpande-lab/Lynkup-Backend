"""Tests for weekly LynkUp reminder preference gating."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.notifications.services import notification_service as ns


@pytest.mark.parametrize(
    ("prefs", "email_prefs", "expected"),
    [
        (None, None, True),
        ({}, None, True),
        ({"weekly_lynkup_request_reminder": True}, None, True),
        ({"weekly_lynkup_request_reminder": False}, None, False),
        ({"WEEKLY_LYNKUP_REQUEST_REMINDER": False}, None, False),
        ({"weekly_lynkup_request_reminder": "false"}, None, False),
        ({"weekly_lynkup_request_reminder": "true"}, None, True),
        ({}, {"weekly_lynkup_request_reminder": False}, False),
        (
            {"weekly_lynkup_request_reminder": True},
            {"weekly_lynkup_request_reminder": False},
            True,
        ),
    ],
)
def test_is_weekly_lynkup_reminder_enabled(prefs, email_prefs, expected):
    assert (
        ns.is_weekly_lynkup_reminder_enabled(prefs, email_preferences=email_prefs)
        is expected
    )


@pytest.mark.asyncio
async def test_create_notification_skips_connection_reminder_when_weekly_off():
    db = AsyncMock()
    preference = SimpleNamespace(
        push_enabled=True,
        in_app_enabled=True,
        category_preferences={"weekly_lynkup_request_reminder": False},
        email_preferences={"bulk_email": True},
    )
    type_row = SimpleNamespace(id=uuid4(), name="CONNECTION_REMINDER")

    with (
        patch.object(ns, "get_notification_type_by_name", AsyncMock(return_value=type_row)),
        patch.object(ns, "_get_or_create_preferences", AsyncMock(return_value=preference)),
        patch.object(ns, "persist_notification", AsyncMock()) as persist,
        patch.object(ns, "get_active_push_targets_for_users", AsyncMock()) as tokens,
        patch.object(ns, "send_push_to_devices", AsyncMock()) as push,
    ):
        result = await ns.create_notification(
            db,
            recipient_user_id=uuid4(),
            notification_type="CONNECTION_REMINDER",
            title="LynkUp Reminder",
            body="pending",
            send_push=True,
        )

    assert result is None
    persist.assert_not_awaited()
    tokens.assert_not_awaited()
    push.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_notification_delivers_connection_reminder_when_weekly_on():
    db = AsyncMock()
    preference = SimpleNamespace(
        push_enabled=True,
        in_app_enabled=True,
        category_preferences={"weekly_lynkup_request_reminder": True},
        email_preferences={"bulk_email": True},
    )
    type_row = SimpleNamespace(id=uuid4(), name="CONNECTION_REMINDER")
    saved = MagicMock()
    saved.id = uuid4()

    with (
        patch.object(ns, "get_notification_type_by_name", AsyncMock(return_value=type_row)),
        patch.object(ns, "_get_or_create_preferences", AsyncMock(return_value=preference)),
        patch.object(ns, "persist_notification", AsyncMock(return_value=saved)) as persist,
        patch.object(
            ns,
            "get_active_push_targets_for_users",
            AsyncMock(return_value=[MagicMock()]),
        ),
        patch.object(
            ns,
            "send_push_to_devices",
            AsyncMock(return_value={"successful_count": 1, "failed_count": 0}),
        ) as push,
        patch.object(
            ns.NotificationPayloadBuilder,
            "build",
            return_value={"notification_type": "CONNECTION_REMINDER"},
        ),
        patch.object(ns.NotificationPayloadBuilder, "for_fcm", return_value={}),
    ):
        result = await ns.create_notification(
            db,
            recipient_user_id=uuid4(),
            notification_type="CONNECTION_REMINDER",
            title="LynkUp Reminder",
            body="pending",
            send_push=True,
        )

    assert result is saved
    persist.assert_awaited_once()
    push.assert_awaited_once()
    db.commit.assert_awaited()
