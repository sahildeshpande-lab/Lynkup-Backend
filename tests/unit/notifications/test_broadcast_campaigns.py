from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.notifications.services import admin_notification_service as admin_svc
from common.enums import NotificationCampaignStatus, NotificationCampaignType


def _campaign(**kwargs):
    defaults = {
        "id": uuid4(),
        "notification_type_id": uuid4(),
        "campaign_type": NotificationCampaignType.announcement,
        "title": "Hello",
        "message": "World",
        "deep_link_payload": None,
        "created_by_admin_id": uuid4(),
        "status": NotificationCampaignStatus.draft,
        "scheduled_at": None,
        "sent_at": None,
        "is_active": True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_list_campaigns_includes_stored_targets(mock_db) -> None:
    db = mock_db()
    campaign = SimpleNamespace(
        id=uuid4(),
        title="AI Workshop",
        message="Register now",
        campaign_type=NotificationCampaignType.topic,
        status=NotificationCampaignStatus.sent,
        scheduled_at=None,
        sent_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        deep_link_payload={
            "targets": [
                {"type": "UNIVERSITY", "values": ["MIT", "Stanford"]},
                {"type": "MAJOR", "values": ["Computer Science"]},
                {
                    "type": "INTERESTS",
                    "values": ["Artificial Intelligence", "Machine Learning"],
                },
            ]
        },
    )

    with (
        patch.object(admin_svc, "count_campaigns", AsyncMock(return_value=1)),
        patch.object(
            admin_svc,
            "get_campaigns",
            AsyncMock(return_value=[(campaign, 0)]),
        ),
    ):
        response = await admin_svc.list_campaigns(db)

    assert response.status is True
    item = response.data["items"][0]
    assert item["targets"] == [
        {"type": "UNIVERSITY", "values": ["MIT", "Stanford"]},
        {"type": "MAJOR", "values": ["Computer Science"]},
        {
            "type": "INTERESTS",
            "values": ["Artificial Intelligence", "Machine Learning"],
        },
    ]


@pytest.mark.asyncio
async def test_list_campaigns_resolves_country_ids_to_country_names(mock_db) -> None:
    db = mock_db()
    country_id = "55555555-1111-1111-1111-000000000006"
    campaign = SimpleNamespace(
        id=uuid4(),
        title="Country campaign",
        message="Hello",
        campaign_type=NotificationCampaignType.topic,
        status=NotificationCampaignStatus.sent,
        scheduled_at=None,
        sent_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        deep_link_payload={
            "targets": [
                {"type": "COUNTRY", "values": [country_id]},
            ]
        },
    )

    with (
        patch.object(admin_svc, "count_campaigns", AsyncMock(return_value=1)),
        patch.object(
            admin_svc,
            "get_campaigns",
            AsyncMock(return_value=[(campaign, 0)]),
        ),
        patch.object(
            admin_svc,
            "_resolve_target_values",
            AsyncMock(return_value=["Canada"]),
        ) as resolve,
    ):
        response = await admin_svc.list_campaigns(db)

    assert response.data["items"][0]["targets"] == [
        {"type": "COUNTRY", "values": ["Canada"]},
    ]
    resolve.assert_awaited()
    assert resolve.await_args.kwargs["values"] == [country_id]


@pytest.mark.asyncio
async def test_resolve_country_values_uses_countries_name(mock_db, scalar_result) -> None:
    country_id = uuid4()
    db = mock_db(
        scalar_result(
            values=[(country_id, "Canada")],
        )
    )

    resolved = await admin_svc._resolve_target_values(
        db,
        target_type=__import__(
            "common.enums", fromlist=["NotificationTargetType"]
        ).NotificationTargetType.country,
        values=[str(country_id).upper()],
    )

    assert resolved == ["Canada"]


@pytest.mark.asyncio
async def test_list_campaigns_returns_empty_targets_when_none_stored(mock_db) -> None:
    db = mock_db()
    campaign = SimpleNamespace(
        id=uuid4(),
        title="All users",
        message="Hello",
        campaign_type=NotificationCampaignType.announcement,
        status=NotificationCampaignStatus.sent,
        scheduled_at=None,
        sent_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        deep_link_payload={"targets": []},
    )

    with (
        patch.object(admin_svc, "count_campaigns", AsyncMock(return_value=1)),
        patch.object(
            admin_svc,
            "get_campaigns",
            AsyncMock(return_value=[(campaign, 42)]),
        ),
    ):
        response = await admin_svc.list_campaigns(db)

    assert response.data["items"][0]["targets"] == []
    assert response.data["items"][0]["recipient_count"] == 42


@pytest.mark.asyncio
async def test_update_campaign_updates_stored_fields(mock_db) -> None:
    from apps.notifications.schemas import UpdateCampaignRequest
    from common.enums import NotificationTargetType

    db = mock_db()
    campaign_id = uuid4()
    campaign = _campaign(
        id=campaign_id,
        campaign_type=NotificationCampaignType.topic,
        deep_link_payload={"targets": [{"type": "MAJOR", "values": ["CS"]}]},
    )
    payload = UpdateCampaignRequest(
        id=campaign_id,
        title="Updated title",
        message="Updated message",
        campaign_type=NotificationCampaignType.topic,
        targets=[
            {
                "type": NotificationTargetType.major,
                "values": ["Data Science"],
            }
        ],
    )

    with (
        patch.object(
            admin_svc,
            "get_campaign_by_id",
            AsyncMock(return_value=campaign),
        ),
        patch.object(
            admin_svc,
            "get_notification_type_by_name",
            AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ),
        patch.object(
            admin_svc,
            "persist_campaign_update",
            AsyncMock(return_value=campaign),
        ) as persist,
        patch.object(
            admin_svc,
            "_sync_broadcast_notification",
            AsyncMock(),
        ),
    ):
        response = await admin_svc.update_campaign(db, payload=payload)

    assert response.status is True
    assert response.message == "Notification campaign updated successfully."
    persist.assert_awaited_once()
    assert persist.await_args.kwargs["title"] == "Updated title"
    assert persist.await_args.kwargs["message"] == "Updated message"


@pytest.mark.asyncio
async def test_delete_campaign_soft_deletes(mock_db) -> None:
    db = mock_db()
    campaign_id = uuid4()
    campaign = _campaign(id=campaign_id, is_active=True)

    with (
        patch.object(
            admin_svc,
            "get_campaign_by_id",
            AsyncMock(return_value=campaign),
        ),
        patch.object(
            admin_svc,
            "deactivate_campaign",
            AsyncMock(return_value=campaign),
        ) as deactivate,
    ):
        response = await admin_svc.delete_campaign(db, campaign_id=campaign_id)

    assert response.status is True
    assert response.message == "Notification campaign deleted successfully."
    deactivate.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_campaign_not_found_when_inactive(mock_db) -> None:
    db = mock_db()
    campaign = _campaign(is_active=False)

    with patch.object(
        admin_svc,
        "get_campaign_by_id",
        AsyncMock(return_value=campaign),
    ):
        response = await admin_svc.delete_campaign(db, campaign_id=campaign.id)

    assert response.status is False
    assert response.message == "Notification campaign not found."


@pytest.mark.asyncio
async def test_dispatch_announcement_creates_single_broadcast(mock_db) -> None:
    db = mock_db()
    campaign = _campaign(campaign_type=NotificationCampaignType.announcement)
    recipients = [uuid4(), uuid4(), uuid4()]

    with (
        patch.object(admin_svc, "_get_campaign", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "resolve_announcement_recipients",
            AsyncMock(return_value=recipients),
        ),
        patch.object(
            admin_svc,
            "create_campaign_audience",
            AsyncMock(return_value=3),
        ) as audience,
        patch.object(
            admin_svc,
            "create_broadcast_notification",
            AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ) as broadcast,
        patch.object(
            admin_svc,
            "get_active_fcm_tokens_for_users",
            AsyncMock(return_value=["t1", "t2"]),
        ),
        patch.object(
            admin_svc,
            "send_push_notifications",
            return_value={"successful_count": 2, "failed_count": 0},
        ) as push,
        patch.object(
            admin_svc,
            "_update_campaign_status",
            AsyncMock(return_value=campaign),
        ),
    ):
        await admin_svc.dispatch_campaign(db, campaign.id)

    audience.assert_awaited_once()
    broadcast.assert_awaited_once()
    assert broadcast.await_args.kwargs["campaign_id"] == campaign.id
    assert broadcast.await_args.kwargs["owner_user_id"] == campaign.created_by_admin_id
    push.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_topic_uses_firebase_topics_not_user_resolution(mock_db) -> None:
    db = mock_db()
    campaign = _campaign(
        campaign_type=NotificationCampaignType.topic,
        deep_link_payload={
            "targets": [
                {"type": "MAJOR", "values": ["Computer Science"]},
                {"type": "INTERESTS", "values": ["AI"]},
            ]
        },
    )
    firebase_topics = {"major_computer_science", "interest_ai"}

    with (
        patch.object(admin_svc, "_get_campaign", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "resolve_firebase_topics_from_targets",
            AsyncMock(return_value=firebase_topics),
        ) as resolve_topics,
        patch.object(
            admin_svc,
            "create_broadcast_notification",
            AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ) as broadcast,
        patch.object(
            admin_svc,
            "send_push_to_topics",
            return_value={"successful_count": 2, "failed_count": 0, "failed_topics": []},
        ) as topic_push,
        patch.object(
            admin_svc,
            "resolve_announcement_recipients",
            AsyncMock(),
        ) as resolve_users,
        patch.object(
            admin_svc,
            "create_campaign_audience",
            AsyncMock(),
        ) as audience,
        patch.object(
            admin_svc,
            "_update_campaign_status",
            AsyncMock(return_value=campaign),
        ),
    ):
        await admin_svc.dispatch_campaign(db, campaign.id)

    resolve_topics.assert_awaited_once()
    broadcast.assert_awaited_once()
    stored_topics = broadcast.await_args.kwargs["deep_link_payload"]["firebase_topics"]
    assert set(stored_topics) == firebase_topics
    topic_push.assert_called_once()
    resolve_users.assert_not_awaited()
    audience.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_notifications_merges_personal_and_broadcasts(mock_db) -> None:
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    personal = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="CONNECTION_REQUEST"),
        notification_type_id=uuid4(),
        campaign_id=None,
        title="Request",
        body="body",
        deep_link_payload=None,
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    announcement = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="ANNOUNCEMENT"),
        notification_type_id=uuid4(),
        campaign_id=uuid4(),
        title="Campus news",
        body="hello",
        deep_link_payload={"broadcast": True},
        is_read=True,
        read_at=None,
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    topic = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="TOPIC"),
        notification_type_id=uuid4(),
        campaign_id=uuid4(),
        title="AI update",
        body="ml",
        deep_link_payload={"firebase_topics": ["interest_ai"]},
        is_read=True,
        read_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    other_topic = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="TOPIC"),
        notification_type_id=uuid4(),
        campaign_id=uuid4(),
        title="Other",
        body="x",
        deep_link_payload={"firebase_topics": ["interest_biology"]},
        is_read=True,
        read_at=None,
        created_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
    )

    with (
        patch.object(
            svc,
            "list_personal_notifications_for_user",
            AsyncMock(return_value=[personal]),
        ),
        patch.object(
            svc,
            "list_broadcast_notifications",
            AsyncMock(return_value=[announcement, topic, other_topic]),
        ),
        patch.object(
            svc,
            "_user_topic_set",
            AsyncMock(return_value={"interest_ai", "major_cs"}),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id)

    assert response.status is True
    items = response.data["items"]
    assert [item["title"] for item in items] == ["Campus news", "Request", "AI update"]
    assert items[0]["is_read"] is False  # broadcast forced unread in API
    assert items[1]["campaign_id"] is None
