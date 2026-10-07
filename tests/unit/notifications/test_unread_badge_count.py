"""Unread badge count helper and push badge wiring tests."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.notifications.db_models import Notification, NotificationType
import apps.notifications.services.notification_service as ns
from core.apns.client import _build_apns_payload


def _preference(
    *,
    user_id=None,
    push_enabled=True,
    in_app_enabled=True,
    category_preferences=None,
    email_preferences=None,
):
    return SimpleNamespace(
        user_id=user_id or uuid4(),
        push_enabled=push_enabled,
        in_app_enabled=in_app_enabled,
        category_preferences=category_preferences or {},
        email_preferences=email_preferences or {"bulk_email": True},
    )


def _personal(user_id, *, is_read=False, type_name="CONNECTION_REQUEST"):
    type_id = uuid4()
    notif_type = NotificationType(id=type_id, name=type_name, is_active=True)
    row = Notification(
        id=uuid4(),
        recipient_user_id=user_id,
        notification_type_id=type_id,
        title="t",
        body="b",
        is_read=is_read,
        created_at=datetime.now(timezone.utc),
    )
    row.notification_type = notif_type
    return row


def _mock_user_created_at(db: AsyncMock, user_ids: list) -> None:
    rows = [
        (user_id, datetime(2020, 1, 1, tzinfo=timezone.utc)) for user_id in user_ids
    ]
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    db.execute = AsyncMock(return_value=result)


@pytest.mark.asyncio
async def test_get_unread_notification_count_zero_when_in_app_disabled():
    db = AsyncMock()
    user_id = uuid4()
    preference = _preference(user_id=user_id, in_app_enabled=False)

    with patch.object(
        ns,
        "count_unread_personal_by_type_for_users",
        AsyncMock(),
    ) as personal_count:
        count = await ns.get_unread_notification_count(
            db, user_id, preference=preference
        )

    assert count == 0
    personal_count.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_unread_matches_totalcount_personal_only():
    db = AsyncMock()
    user_id = uuid4()
    preference = _preference(user_id=user_id)
    unread = _personal(user_id, is_read=False)
    read = _personal(user_id, is_read=True)
    _mock_user_created_at(db, [user_id])

    with (
        patch.object(
            ns,
            "count_unread_personal_by_type_for_users",
            AsyncMock(return_value={user_id: {"CONNECTION_REQUEST": 1}}),
        ),
        patch.object(ns, "list_broadcast_notifications", AsyncMock(return_value=[])),
        patch.object(ns, "get_campaign_audience_for_users", AsyncMock(return_value={})),
        patch.object(
            ns,
            "_merged_category_preferences",
            AsyncMock(return_value={"CONNECTION_REQUEST": True}),
        ),
        patch.object(
            ns,
            "_list_unified_notifications_for_user",
            AsyncMock(
                return_value=[
                    (unread, False, False, None),
                    (read, False, True, read.read_at),
                ]
            ),
        ),
    ):
        helper_count = await ns.get_unread_notification_count(
            db, user_id, preference=preference
        )
        list_unread = sum(
            1
            for _, _, is_read_value, _ in await ns._list_unified_notifications_for_user(
                db, user_id, preference=preference
            )
            if not is_read_value
        )

    assert helper_count == 1
    assert helper_count == list_unread


@pytest.mark.asyncio
async def test_get_unread_respects_disabled_category():
    db = AsyncMock()
    user_id = uuid4()
    preference = _preference(
        user_id=user_id,
        category_preferences={"CONNECTION_REQUEST": False},
    )
    _mock_user_created_at(db, [user_id])

    with (
        patch.object(
            ns,
            "count_unread_personal_by_type_for_users",
            AsyncMock(return_value={user_id: {"CONNECTION_REQUEST": 7}}),
        ),
        patch.object(ns, "list_broadcast_notifications", AsyncMock(return_value=[])),
        patch.object(ns, "get_campaign_audience_for_users", AsyncMock(return_value={})),
        patch.object(
            ns,
            "_merged_category_preferences",
            AsyncMock(return_value={"CONNECTION_REQUEST": False}),
        ),
    ):
        count = await ns.get_unread_notification_count(
            db, user_id, preference=preference
        )

    assert count == 0


@pytest.mark.asyncio
async def test_get_unread_counts_for_users_are_user_scoped():
    db = AsyncMock()
    user_a = uuid4()
    user_b = uuid4()
    pref_a = _preference(user_id=user_a)
    pref_b = _preference(user_id=user_b)
    _mock_user_created_at(db, [user_a, user_b])

    with (
        patch.object(
            ns,
            "get_preferences_for_users",
            AsyncMock(return_value={user_a: pref_a, user_b: pref_b}),
        ),
        patch.object(
            ns,
            "count_unread_personal_by_type_for_users",
            AsyncMock(
                return_value={
                    user_a: {"CONNECTION_REQUEST": 5},
                    user_b: {"CONNECTION_REQUEST": 12},
                }
            ),
        ),
        patch.object(ns, "list_broadcast_notifications", AsyncMock(return_value=[])),
        patch.object(ns, "get_campaign_audience_for_users", AsyncMock(return_value={})),
        patch.object(
            ns,
            "_merged_category_preferences",
            AsyncMock(return_value={"CONNECTION_REQUEST": True}),
        ),
    ):
        counts = await ns.get_unread_notification_counts_for_users(
            db, [user_a, user_b]
        )

    assert counts[user_a] == 5
    assert counts[user_b] == 12


@pytest.mark.asyncio
async def test_get_unread_includes_unread_broadcast_announcement():
    db = AsyncMock()
    user_id = uuid4()
    preference = _preference(user_id=user_id)
    campaign_id = uuid4()
    type_id = uuid4()
    notif_type = NotificationType(id=type_id, name="ANNOUNCEMENT", is_active=True)
    broadcast = Notification(
        id=uuid4(),
        recipient_user_id=uuid4(),
        notification_type_id=type_id,
        campaign_id=campaign_id,
        title="Announcement",
        body="Hello",
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )
    broadcast.notification_type = notif_type
    # Avoid SQLAlchemy relationship setter; list helper reads __dict__["campaign"].
    broadcast.__dict__["campaign"] = SimpleNamespace(
        sent_at=datetime.now(timezone.utc),
        is_active=True,
    )
    _mock_user_created_at(db, [user_id])

    with (
        patch.object(
            ns,
            "count_unread_personal_by_type_for_users",
            AsyncMock(return_value={user_id: {}}),
        ),
        patch.object(
            ns, "list_broadcast_notifications", AsyncMock(return_value=[broadcast])
        ),
        patch.object(ns, "get_campaign_audience_for_users", AsyncMock(return_value={})),
        patch.object(
            ns,
            "_merged_category_preferences",
            AsyncMock(return_value={"ANNOUNCEMENT": True}),
        ),
    ):
        count = await ns.get_unread_notification_count(
            db, user_id, preference=preference
        )

    assert count == 1


@pytest.mark.asyncio
async def test_create_notification_commits_before_push_and_sets_badge():
    db = AsyncMock()
    preference = _preference(push_enabled=True, in_app_enabled=True)
    type_row = SimpleNamespace(id=uuid4(), name="CONNECTION_REQUEST")
    saved = MagicMock()
    saved.id = uuid4()
    recipient = uuid4()

    commit_order: list[str] = []

    async def _commit():
        commit_order.append("commit")

    async def _push(*_args, **_kwargs):
        commit_order.append("push")
        return {"successful_count": 2, "failed_count": 0}

    db.commit = AsyncMock(side_effect=_commit)
    db.refresh = AsyncMock()

    with (
        patch.object(ns, "get_notification_type_by_name", AsyncMock(return_value=type_row)),
        patch.object(ns, "_get_or_create_preferences", AsyncMock(return_value=preference)),
        patch.object(ns, "_is_category_enabled", AsyncMock(return_value=True)),
        patch.object(ns, "persist_notification", AsyncMock(return_value=saved)),
        patch.object(
            ns,
            "get_active_push_targets_for_users",
            AsyncMock(
                return_value=[
                    ("android-token", "android"),
                    ("ios-token", "ios"),
                ]
            ),
        ),
        patch.object(ns, "get_unread_notification_count", AsyncMock(return_value=7)),
        patch.object(ns, "send_push_to_devices", AsyncMock(side_effect=_push)) as push,
        patch.object(
            ns.NotificationPayloadBuilder,
            "build",
            return_value={"notification_type": "CONNECTION_REQUEST"},
        ),
        patch.object(
            ns.NotificationPayloadBuilder,
            "for_fcm",
            side_effect=lambda payload: {k: str(v) for k, v in payload.items()},
        ),
    ):
        result = await ns.create_notification(
            db,
            recipient_user_id=recipient,
            notification_type="CONNECTION_REQUEST",
            title="Request",
            body="pending",
            send_push=True,
        )

    assert result is saved
    assert commit_order == ["commit", "push"]
    push.assert_awaited_once()
    assert push.await_args.kwargs.get("badge") == 7
    assert push.await_args.args[3]["unread_count"] == "7"


@pytest.mark.asyncio
async def test_create_notification_skips_push_when_commit_fails():
    db = AsyncMock()
    preference = _preference()
    type_row = SimpleNamespace(id=uuid4(), name="CONNECTION_REQUEST")
    saved = MagicMock()
    saved.id = uuid4()
    db.commit = AsyncMock(side_effect=RuntimeError("db down"))
    db.rollback = AsyncMock()

    with (
        patch.object(ns, "get_notification_type_by_name", AsyncMock(return_value=type_row)),
        patch.object(ns, "_get_or_create_preferences", AsyncMock(return_value=preference)),
        patch.object(ns, "_is_category_enabled", AsyncMock(return_value=True)),
        patch.object(ns, "persist_notification", AsyncMock(return_value=saved)),
        patch.object(ns, "send_push_to_devices", AsyncMock()) as push,
        patch.object(
            ns.NotificationPayloadBuilder,
            "build",
            return_value={"notification_type": "CONNECTION_REQUEST"},
        ),
    ):
        result = await ns.create_notification(
            db,
            recipient_user_id=uuid4(),
            notification_type="CONNECTION_REQUEST",
            title="Request",
            body="pending",
            send_push=True,
        )

    assert result is None
    push.assert_not_awaited()
    db.rollback.assert_awaited()


def test_apns_payload_sets_badge_including_zero():
    payload = _build_apns_payload("t", "b", {"unread_count": "0"}, badge=0)
    assert payload["aps"]["badge"] == 0
    assert payload["unread_count"] == "0"

    payload_seven = _build_apns_payload("t", "b", {"unread_count": "7"}, badge=7)
    assert payload_seven["aps"]["badge"] == 7


def test_apns_payload_omits_badge_when_not_provided():
    payload = _build_apns_payload("t", "b", {"k": "v"})
    assert "badge" not in payload["aps"]


@pytest.mark.asyncio
async def test_send_push_notification_sets_android_notification_count():
    from core.auth import services as auth_services

    captured = {}

    class _FakeMessage:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    with (
        patch.object(auth_services, "initialize_firebase_app"),
        patch.object(auth_services.messaging, "Message", side_effect=_FakeMessage),
        patch.object(
            auth_services.messaging,
            "Notification",
            side_effect=lambda **kwargs: ("notification", kwargs),
        ),
        patch.object(
            auth_services.messaging,
            "AndroidConfig",
            side_effect=lambda **kwargs: ("android", kwargs),
        ),
        patch.object(
            auth_services.messaging,
            "AndroidNotification",
            side_effect=lambda **kwargs: kwargs,
        ),
        patch.object(
            auth_services.messaging,
            "APNSConfig",
            side_effect=lambda **kwargs: ("apns", kwargs),
        ),
        patch.object(
            auth_services.messaging,
            "APNSPayload",
            side_effect=lambda **kwargs: kwargs,
        ),
        patch.object(
            auth_services.messaging,
            "Aps",
            side_effect=lambda **kwargs: kwargs,
        ),
        patch.object(auth_services.messaging, "send", return_value="mid-1"),
    ):
        message_id = auth_services.send_push_notification(
            "token-1",
            "Title",
            "Body",
            {"unread_count": "1"},
            badge=1,
        )

    assert message_id == "mid-1"
    android = captured["android"][1]
    assert android["notification"]["notification_count"] == 1
    assert android["notification"]["channel_id"] == "kampulynk_alerts_v3"
    apns_payload = captured["apns"][1]["payload"]
    assert apns_payload["aps"]["badge"] == 1


@pytest.mark.asyncio
async def test_send_push_notification_sets_badge_zero():
    from core.auth import services as auth_services

    captured = {}

    class _FakeMessage:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    with (
        patch.object(auth_services, "initialize_firebase_app"),
        patch.object(auth_services.messaging, "Message", side_effect=_FakeMessage),
        patch.object(
            auth_services.messaging,
            "Notification",
            side_effect=lambda **kwargs: ("notification", kwargs),
        ),
        patch.object(
            auth_services.messaging,
            "AndroidConfig",
            side_effect=lambda **kwargs: ("android", kwargs),
        ),
        patch.object(
            auth_services.messaging,
            "AndroidNotification",
            side_effect=lambda **kwargs: kwargs,
        ),
        patch.object(
            auth_services.messaging,
            "APNSConfig",
            side_effect=lambda **kwargs: ("apns", kwargs),
        ),
        patch.object(
            auth_services.messaging,
            "APNSPayload",
            side_effect=lambda **kwargs: kwargs,
        ),
        patch.object(
            auth_services.messaging,
            "Aps",
            side_effect=lambda **kwargs: kwargs,
        ),
        patch.object(auth_services.messaging, "send", return_value="mid-0"),
    ):
        auth_services.send_push_notification(
            "token-1", "Title", "Body", {"unread_count": "0"}, badge=0
        )

    assert captured["android"][1]["notification"]["notification_count"] == 0
    assert captured["apns"][1]["payload"]["aps"]["badge"] == 0
