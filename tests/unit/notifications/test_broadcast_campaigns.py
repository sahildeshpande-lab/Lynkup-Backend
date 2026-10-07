from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.notifications.schemas import CampaignTargetUserValue
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
        {"type": "UNIVERSITY", "to_all": False, "values": ["MIT", "Stanford"]},
        {"type": "MAJOR", "to_all": False, "values": ["Computer Science"]},
        {
            "type": "INTERESTS",
            "to_all": False,
            "values": ["Artificial Intelligence", "Machine Learning"],
        },
    ]


@pytest.mark.asyncio
async def test_list_campaigns_includes_users_is_alumni_target(mock_db) -> None:
    db = mock_db()
    campaign = SimpleNamespace(
        id=uuid4(),
        title="Alumni notice",
        message="Hello alumni",
        campaign_type=NotificationCampaignType.topic,
        status=NotificationCampaignStatus.sent,
        scheduled_at=None,
        sent_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        deep_link_payload={
            "targets": [
                {"type": "UNIVERSITY", "to_all": True, "values": []},
                {"type": "USERS", "to_all": True, "values": [], "is_alumni": True},
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

    assert response.data["items"][0]["targets"] == [
        {"type": "UNIVERSITY", "to_all": True, "values": []},
        {"type": "USERS", "to_all": True, "values": [], "is_alumni": True},
    ]


@pytest.mark.asyncio
async def test_list_campaigns_enriches_users_target_values(mock_db) -> None:
    db = mock_db()
    user_a = uuid4()
    user_b = uuid4()
    campaign = SimpleNamespace(
        id=uuid4(),
        title="User notice",
        message="Hello users",
        campaign_type=NotificationCampaignType.topic,
        status=NotificationCampaignStatus.sent,
        scheduled_at=None,
        sent_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        deep_link_payload={
            "targets": [
                {
                    "type": "USERS",
                    "to_all": False,
                    "values": [str(user_a), str(user_b)],
                    "is_alumni": True,
                }
            ]
        },
    )

    with (
        patch.object(admin_svc, "count_campaigns", AsyncMock(return_value=1)),
        patch.object(
            admin_svc,
            "get_campaigns",
            AsyncMock(return_value=[(campaign, 2)]),
        ),
        patch.object(
            admin_svc,
            "_resolve_user_target_values",
            AsyncMock(
                return_value=[
                    CampaignTargetUserValue(
                        id=str(user_a),
                        firstName="AAAAAAAAAa",
                        lastName="ZZZZZZZZZZZz",
                    ),
                    CampaignTargetUserValue(
                        id=str(user_b),
                        firstName="BBBBBBBbbb",
                        lastName="YYYYYYyyyy",
                    ),
                ]
            ),
        ) as resolve_users,
    ):
        response = await admin_svc.list_campaigns(db)

    assert response.data["items"][0]["targets"] == [
        {
            "type": "USERS",
            "to_all": False,
            "values": [
                {
                    "id": str(user_a),
                    "firstName": "AAAAAAAAAa",
                    "lastName": "ZZZZZZZZZZZz",
                },
                {
                    "id": str(user_b),
                    "firstName": "BBBBBBBbbb",
                    "lastName": "YYYYYYyyyy",
                },
            ],
            "is_alumni": True,
        }
    ]
    resolve_users.assert_awaited_once()
    assert resolve_users.await_args.args[1] == [str(user_a), str(user_b)]


@pytest.mark.asyncio
async def test_resolve_user_target_values_uses_profile_names(
    mock_db, scalar_result
) -> None:
    user_id = uuid4()
    missing_id = uuid4()
    db = mock_db(
        scalar_result(
            values=[(user_id, "AAAAAAAAAa", "ZZZZZZZZZZZz")],
        )
    )

    resolved = await admin_svc._resolve_user_target_values(
        db,
        [str(user_id).upper(), str(missing_id)],
    )

    assert resolved == [
        CampaignTargetUserValue(
            id=str(user_id),
            firstName="AAAAAAAAAa",
            lastName="ZZZZZZZZZZZz",
        ),
        CampaignTargetUserValue(
            id=str(missing_id),
            firstName="",
            lastName="",
        ),
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
                {"type": "COUNTRY", "to_all": False, "values": [country_id]},
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
        {"type": "COUNTRY", "to_all": False, "values": ["Canada"]},
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
    user_a, user_b = recipients[0], recipients[1]

    with (
        patch.object(admin_svc, "get_campaign_for_dispatch", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "get_notification_by_campaign_id",
            AsyncMock(return_value=None),
        ),
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
            AsyncMock(
                return_value=SimpleNamespace(
                    id=uuid4(),
                    deep_link_payload={
                        "broadcast": True,
                        "campaign_type": "ANNOUNCEMENT",
                        "campaign_id": str(campaign.id),
                    },
                )
            ),
        ) as broadcast,
        patch.object(
            admin_svc,
            "filter_users_eligible_for_push",
            AsyncMock(return_value=[user_a, user_b]),
        ),
        patch.object(
            admin_svc,
            "get_active_push_targets_grouped_by_user",
            AsyncMock(
                return_value={
                    user_a: [("t1", "android")],
                    user_b: [("t2", "android")],
                }
            ),
        ),
        patch.object(
            admin_svc,
            "get_unread_notification_counts_for_users",
            AsyncMock(return_value={user_a: 5, user_b: 12}),
        ),
        patch.object(
            admin_svc,
            "send_push_to_devices",
            AsyncMock(return_value={"successful_count": 1, "failed_count": 0}),
        ) as push,
        patch.object(
            admin_svc,
            "_update_campaign_status",
            AsyncMock(return_value=campaign),
        ),
        patch.object(
            admin_svc,
            "_log_campaign_dispatch_activity",
            AsyncMock(),
        ) as activity_log,
    ):
        await admin_svc.dispatch_campaign(db, campaign.id)

    activity_log.assert_awaited_once()
    assert activity_log.await_args.kwargs["status"] == NotificationCampaignStatus.sent
    audience.assert_awaited_once()
    broadcast.assert_awaited_once()
    assert broadcast.await_args.kwargs["campaign_id"] == campaign.id
    assert broadcast.await_args.kwargs["owner_user_id"] == campaign.created_by_admin_id
    assert push.await_count == 2
    badges = {call.kwargs.get("badge") for call in push.await_args_list}
    assert badges == {5, 12}
    fcm_data = push.await_args_list[0].args[3]
    assert fcm_data["notification_type"] == "ANNOUNCEMENT"
    assert "deep_link" in fcm_data
    assert json.loads(fcm_data["deep_link"]) == {"screen": "notifications"}
    assert "unread_count" in fcm_data


@pytest.mark.asyncio
async def test_dispatch_topic_sends_one_deduped_token_push_per_recipient(mock_db) -> None:
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
    recipient_ids = [uuid4(), uuid4()]
    notification_id = uuid4()

    with (
        patch.object(admin_svc, "get_campaign_for_dispatch", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "get_notification_by_campaign_id",
            AsyncMock(return_value=None),
        ),
        patch.object(
            admin_svc,
            "resolve_firebase_topics_from_targets",
            AsyncMock(return_value=firebase_topics),
        ) as resolve_topics,
        patch.object(
            admin_svc,
            "resolve_topic_recipients",
            AsyncMock(return_value=recipient_ids),
        ) as resolve_recipients,
        patch.object(
            admin_svc,
            "create_broadcast_notification",
            AsyncMock(
                return_value=SimpleNamespace(
                    id=notification_id,
                    deep_link_payload={
                        "broadcast": True,
                        "campaign_type": "TOPIC",
                        "campaign_id": str(campaign.id),
                        "firebase_topics": sorted(firebase_topics),
                    },
                )
            ),
        ) as broadcast,
        patch.object(
            admin_svc,
            "filter_users_eligible_for_push",
            AsyncMock(return_value=recipient_ids),
        ),
        patch.object(
            admin_svc,
            "get_active_push_targets_grouped_by_user",
            AsyncMock(
                return_value={
                    recipient_ids[0]: [("t1", "android")],
                    recipient_ids[1]: [("t2", "android")],
                }
            ),
        ) as load_tokens,
        patch.object(
            admin_svc,
            "get_unread_notification_counts_for_users",
            AsyncMock(
                return_value={recipient_ids[0]: 3, recipient_ids[1]: 7},
            ),
        ),
        patch.object(
            admin_svc,
            "send_push_to_devices",
            AsyncMock(return_value={"successful_count": 1, "failed_count": 0}),
        ) as token_push,
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
        patch.object(
            admin_svc,
            "_log_campaign_dispatch_activity",
            AsyncMock(),
        ),
    ):
        await admin_svc.dispatch_campaign(db, campaign.id)

    resolve_topics.assert_awaited_once()
    resolve_recipients.assert_awaited_once()
    audience.assert_awaited_once_with(
        db,
        campaign_id=campaign.id,
        user_ids=recipient_ids,
    )
    broadcast.assert_awaited_once()
    stored_topics = broadcast.await_args.kwargs["deep_link_payload"]["firebase_topics"]
    assert set(stored_topics) == firebase_topics
    load_tokens.assert_awaited_once()
    assert load_tokens.await_args.args[1] == recipient_ids
    assert token_push.await_count == 2
    assert {call.kwargs.get("badge") for call in token_push.await_args_list} == {3, 7}
    resolve_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_topic_skips_firebase_topic_fanout_when_no_recipients(
    mock_db,
) -> None:
    """Empty audience must not publish to each Firebase topic (avoids N pushes)."""
    db = mock_db()
    campaign = _campaign(
        campaign_type=NotificationCampaignType.topic,
        deep_link_payload={
            "targets": [
                {"type": "UNIVERSITY", "values": ["u1"]},
                {"type": "MAJOR", "values": ["CS"]},
                {"type": "MINOR", "values": ["Math"]},
            ]
        },
    )
    firebase_topics = {"university_u1", "major_cs", "minor_math"}

    with (
        patch.object(admin_svc, "get_campaign_for_dispatch", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "get_notification_by_campaign_id",
            AsyncMock(return_value=None),
        ),
        patch.object(admin_svc, "_get_campaign", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "resolve_firebase_topics_from_targets",
            AsyncMock(return_value=firebase_topics),
        ),
        patch.object(
            admin_svc,
            "resolve_topic_recipients",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            admin_svc,
            "create_campaign_audience",
            AsyncMock(),
        ),
        patch.object(
            admin_svc,
            "create_broadcast_notification",
            AsyncMock(
                return_value=SimpleNamespace(
                    id=uuid4(),
                    deep_link_payload={
                        "broadcast": True,
                        "campaign_type": "TOPIC",
                        "campaign_id": str(campaign.id),
                        "firebase_topics": sorted(firebase_topics),
                    },
                )
            ),
        ),
        patch.object(
            admin_svc,
            "get_active_push_targets_grouped_by_user",
            AsyncMock(),
        ) as load_tokens,
        patch.object(
            admin_svc,
            "send_push_to_devices",
            AsyncMock(),
        ) as token_push,
        patch.object(
            admin_svc,
            "_send_token_push",
            AsyncMock(),
        ) as send_token,
        patch.object(
            admin_svc,
            "_update_campaign_status",
            AsyncMock(return_value=campaign),
        ),
        patch.object(
            admin_svc,
            "_log_campaign_dispatch_activity",
            AsyncMock(),
        ) as activity_log,
    ):
        with pytest.raises(ValueError, match="No eligible users"):
            await admin_svc.dispatch_campaign(db, campaign.id)

    load_tokens.assert_not_awaited()
    token_push.assert_not_awaited()
    send_token.assert_not_awaited()
    activity_log.assert_awaited_once()
    assert activity_log.await_args.kwargs["status"] == NotificationCampaignStatus.failed


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
            "_get_or_create_preferences",
            AsyncMock(
                return_value=SimpleNamespace(
                    in_app_enabled=True,
                    category_preferences={
                        "CONNECTION_REQUEST": True,
                        "ANNOUNCEMENT": True,
                        "TOPIC": True,
                    },
                )
            ),
        ),
        patch.object(
            svc,
            "get_default_category_preferences",
            AsyncMock(
                return_value={
                    "CONNECTION_REQUEST": True,
                    "ANNOUNCEMENT": True,
                    "TOPIC": True,
                }
            ),
        ),
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
        patch.object(
            svc,
            "get_campaign_audience_for_user",
            AsyncMock(return_value={}),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id)

    assert response.status is True
    items = response.data["items"]
    assert [item["title"] for item in items] == ["Campus news", "Request", "AI update"]
    assert items[0]["is_read"] is False
    assert items[1]["campaign_id"] is None


@pytest.mark.asyncio
async def test_list_notifications_shows_topic_campaign_when_user_in_audience_only(
    mock_db,
) -> None:
    """USERS-target topic campaigns may have empty firebase_topics; audience still counts."""
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    campaign_id = uuid4()
    users_only_topic = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="TOPIC"),
        notification_type_id=uuid4(),
        campaign_id=campaign_id,
        title="Hand-picked",
        body="hello",
        deep_link_payload={"firebase_topics": []},
        is_read=True,
        read_at=None,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )

    with (
        patch.object(
            svc,
            "_get_or_create_preferences",
            AsyncMock(
                return_value=SimpleNamespace(
                    in_app_enabled=True,
                    category_preferences={"TOPIC": True},
                )
            ),
        ),
        patch.object(
            svc,
            "get_default_category_preferences",
            AsyncMock(return_value={"TOPIC": True}),
        ),
        patch.object(
            svc,
            "list_personal_notifications_for_user",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            svc,
            "list_broadcast_notifications",
            AsyncMock(return_value=[users_only_topic]),
        ),
        patch.object(
            svc,
            "_user_topic_set",
            AsyncMock(return_value=set()),
        ),
        patch.object(
            svc,
            "get_campaign_audience_for_user",
            AsyncMock(return_value={campaign_id: SimpleNamespace(is_read=False, read_at=None)}),
        ),
        patch.object(
            svc,
            "_user_registered_at",
            AsyncMock(return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id)

    assert response.status is True
    assert [item["title"] for item in response.data["items"]] == ["Hand-picked"]


@pytest.mark.asyncio
async def test_list_notifications_hides_broadcasts_before_user_registration(
    mock_db,
) -> None:
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    user_registered_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    historical = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="ANNOUNCEMENT"),
        notification_type_id=uuid4(),
        campaign_id=uuid4(),
        title="Old campus news",
        body="before signup",
        deep_link_payload={"broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        campaign=None,
    )
    recent = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="ANNOUNCEMENT"),
        notification_type_id=uuid4(),
        campaign_id=uuid4(),
        title="New campus news",
        body="after signup",
        deep_link_payload={"broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 2, 2, tzinfo=timezone.utc),
        campaign=None,
    )

    with (
        patch.object(
            svc,
            "_get_or_create_preferences",
            AsyncMock(
                return_value=SimpleNamespace(
                    in_app_enabled=True,
                    category_preferences={"ANNOUNCEMENT": True},
                )
            ),
        ),
        patch.object(
            svc,
            "list_personal_notifications_for_user",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            svc,
            "list_broadcast_notifications",
            AsyncMock(return_value=[recent, historical]),
        ),
        patch.object(
            svc,
            "_user_registered_at",
            AsyncMock(return_value=user_registered_at),
        ),
        patch.object(
            svc,
            "get_campaign_audience_for_user",
            AsyncMock(return_value={}),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id)

    assert response.status is True
    items = response.data["items"]
    assert [item["title"] for item in items] == ["New campus news"]
    assert items[0]["is_read"] is False


@pytest.mark.asyncio
async def test_list_notifications_empty_for_new_user_with_only_historical_globals(
    mock_db,
) -> None:
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    historical = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="ANNOUNCEMENT"),
        notification_type_id=uuid4(),
        campaign_id=uuid4(),
        title="Old campus news",
        body="before signup",
        deep_link_payload={"broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        campaign=None,
    )

    with (
        patch.object(
            svc,
            "_get_or_create_preferences",
            AsyncMock(
                return_value=SimpleNamespace(
                    in_app_enabled=True,
                    category_preferences={"ANNOUNCEMENT": True},
                )
            ),
        ),
        patch.object(
            svc,
            "list_personal_notifications_for_user",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            svc,
            "list_broadcast_notifications",
            AsyncMock(return_value=[historical]),
        ),
        patch.object(
            svc,
            "_user_registered_at",
            AsyncMock(return_value=datetime(2026, 2, 1, tzinfo=timezone.utc)),
        ),
        patch.object(
            svc,
            "get_campaign_audience_for_user",
            AsyncMock(return_value={}),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id)
        unread = await svc.list_notifications(db, user_id=user_id, is_read=False)

    assert response.status is True
    assert response.data["items"] == []
    assert unread.data["items"] == []


@pytest.mark.asyncio
async def test_mark_as_read_rejects_broadcast_before_user_registration(
    mock_db,
) -> None:
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    notification_id = uuid4()
    broadcast = SimpleNamespace(
        id=notification_id,
        notification_type=SimpleNamespace(name="ANNOUNCEMENT"),
        notification_type_id=uuid4(),
        campaign_id=uuid4(),
        title="Old campus news",
        body="before signup",
        deep_link_payload={"broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        campaign=None,
    )

    with (
        patch.object(
            svc,
            "get_notification_for_user",
            AsyncMock(return_value=None),
        ),
        patch.object(
            svc,
            "get_broadcast_notification_by_id",
            AsyncMock(return_value=broadcast),
        ),
        patch.object(
            svc,
            "_user_registered_at",
            AsyncMock(return_value=datetime(2026, 2, 1, tzinfo=timezone.utc)),
        ),
        patch.object(
            svc,
            "_user_topic_set",
            AsyncMock(return_value=set()),
        ),
        patch.object(
            svc,
            "mark_campaign_audience_read",
            AsyncMock(),
        ) as mark_audience,
    ):
        response = await svc.mark_as_read(
            db,
            user_id=user_id,
            notification_id=notification_id,
        )

    mark_audience.assert_not_awaited()
    assert response.status is False
    assert response.message == "Notification not found."


@pytest.mark.asyncio
async def test_mark_as_read_topic_campaign_when_user_in_audience_only(
    mock_db,
) -> None:
    """USERS-target TOPIC with empty firebase_topics is readable via audience."""
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    campaign_id = uuid4()
    notification_id = uuid4()
    read_at = datetime(2026, 3, 2, tzinfo=timezone.utc)
    broadcast = SimpleNamespace(
        id=notification_id,
        notification_type=SimpleNamespace(name="TOPIC"),
        notification_type_id=uuid4(),
        campaign_id=campaign_id,
        title="Hand-picked",
        body="hello",
        deep_link_payload={"firebase_topics": [], "broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        campaign=None,
    )
    audience = SimpleNamespace(
        campaign_id=campaign_id,
        is_read=True,
        read_at=read_at,
    )

    with (
        patch.object(
            svc,
            "get_notification_for_user",
            AsyncMock(return_value=None),
        ),
        patch.object(
            svc,
            "get_broadcast_notification_by_id",
            AsyncMock(return_value=broadcast),
        ),
        patch.object(
            svc,
            "_user_registered_at",
            AsyncMock(return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ),
        patch.object(
            svc,
            "get_campaign_audience_for_user",
            AsyncMock(
                return_value={
                    campaign_id: SimpleNamespace(is_read=False, read_at=None),
                }
            ),
        ),
        patch.object(
            svc,
            "_user_topic_set",
            AsyncMock(return_value=set()),
        ),
        patch.object(
            svc,
            "mark_campaign_audience_read",
            AsyncMock(return_value=audience),
        ) as mark_audience,
    ):
        response = await svc.mark_as_read(
            db,
            user_id=user_id,
            notification_id=notification_id,
        )

    mark_audience.assert_awaited_once_with(
        db,
        user_id=user_id,
        campaign_id=campaign_id,
    )
    db.commit.assert_awaited_once()
    assert response.status is True
    assert response.data.is_read is True


@pytest.mark.asyncio
async def test_mark_as_read_rejects_topic_without_audience_or_topic_match(
    mock_db,
) -> None:
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    campaign_id = uuid4()
    notification_id = uuid4()
    broadcast = SimpleNamespace(
        id=notification_id,
        notification_type=SimpleNamespace(name="TOPIC"),
        notification_type_id=uuid4(),
        campaign_id=campaign_id,
        title="Hand-picked",
        body="hello",
        deep_link_payload={"firebase_topics": [], "broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        campaign=None,
    )

    with (
        patch.object(
            svc,
            "get_notification_for_user",
            AsyncMock(return_value=None),
        ),
        patch.object(
            svc,
            "get_broadcast_notification_by_id",
            AsyncMock(return_value=broadcast),
        ),
        patch.object(
            svc,
            "_user_registered_at",
            AsyncMock(return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ),
        patch.object(
            svc,
            "get_campaign_audience_for_user",
            AsyncMock(return_value={}),
        ),
        patch.object(
            svc,
            "_user_topic_set",
            AsyncMock(return_value=set()),
        ),
        patch.object(
            svc,
            "mark_campaign_audience_read",
            AsyncMock(),
        ) as mark_audience,
    ):
        response = await svc.mark_as_read(
            db,
            user_id=user_id,
            notification_id=notification_id,
        )

    mark_audience.assert_not_awaited()
    assert response.status is False
    assert response.message == "Notification not found."


@pytest.mark.asyncio
async def test_list_notifications_hides_disabled_announcement_category(mock_db) -> None:
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    announcement = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="ANNOUNCEMENT"),
        notification_type_id=uuid4(),
        campaign_id=uuid4(),
        title="Campus news",
        body="hello",
        deep_link_payload={"broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
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

    with (
        patch.object(
            svc,
            "_get_or_create_preferences",
            AsyncMock(
                return_value=SimpleNamespace(
                    in_app_enabled=True,
                    category_preferences={
                        "CONNECTION_REQUEST": True,
                        "ANNOUNCEMENT": False,
                        "TOPIC": True,
                    },
                )
            ),
        ),
        patch.object(
            svc,
            "get_default_category_preferences",
            AsyncMock(
                return_value={
                    "CONNECTION_REQUEST": True,
                    "ANNOUNCEMENT": True,
                    "TOPIC": True,
                }
            ),
        ),
        patch.object(
            svc,
            "list_personal_notifications_for_user",
            AsyncMock(return_value=[personal]),
        ),
        patch.object(
            svc,
            "list_broadcast_notifications",
            AsyncMock(return_value=[announcement]),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id)

    titles = [item["title"] for item in response.data["items"]]
    assert titles == ["Request"]
    assert "Campus news" not in titles


@pytest.mark.asyncio
async def test_dispatch_announcement_skips_push_for_opted_out_users(mock_db) -> None:
    db = mock_db()
    campaign = _campaign(campaign_type=NotificationCampaignType.announcement)
    recipients = [uuid4(), uuid4(), uuid4()]
    push_eligible = [recipients[0], recipients[2]]

    with (
        patch.object(admin_svc, "get_campaign_for_dispatch", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "get_notification_by_campaign_id",
            AsyncMock(return_value=None),
        ),
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
            AsyncMock(
                return_value=SimpleNamespace(
                    id=uuid4(),
                    deep_link_payload={
                        "broadcast": True,
                        "campaign_type": "ANNOUNCEMENT",
                        "campaign_id": str(campaign.id),
                    },
                )
            ),
        ),
        patch.object(
            admin_svc,
            "filter_users_eligible_for_push",
            AsyncMock(return_value=push_eligible),
        ) as filter_push,
        patch.object(
            admin_svc,
            "get_active_push_targets_grouped_by_user",
            AsyncMock(
                return_value={
                    push_eligible[0]: [("t1", "android")],
                }
            ),
        ) as load_tokens,
        patch.object(
            admin_svc,
            "get_unread_notification_counts_for_users",
            AsyncMock(return_value={push_eligible[0]: 1}),
        ),
        patch.object(
            admin_svc,
            "send_push_to_devices",
            AsyncMock(return_value={"successful_count": 1, "failed_count": 0}),
        ) as push,
        patch.object(
            admin_svc,
            "_update_campaign_status",
            AsyncMock(return_value=campaign),
        ),
        patch.object(
            admin_svc,
            "_log_campaign_dispatch_activity",
            AsyncMock(),
        ),
    ):
        await admin_svc.dispatch_campaign(db, campaign.id)

    filter_push.assert_awaited_once_with(
        db,
        recipients,
        category="ANNOUNCEMENT",
    )
    audience.assert_awaited_once_with(
        db,
        campaign_id=campaign.id,
        user_ids=recipients,
    )
    load_tokens.assert_awaited_once_with(db, push_eligible)
    push.assert_awaited_once()
    assert push.await_args.kwargs.get("badge") == 1


@pytest.mark.asyncio
async def test_list_notifications_uses_campaign_audience_read_state(mock_db) -> None:
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    campaign_id = uuid4()
    read_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    announcement = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="ANNOUNCEMENT"),
        notification_type_id=uuid4(),
        campaign_id=campaign_id,
        title="Campus news",
        body="hello",
        deep_link_payload={"broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    audience = SimpleNamespace(
        campaign_id=campaign_id,
        is_read=True,
        read_at=read_at,
    )

    with (
        patch.object(
            svc,
            "_get_or_create_preferences",
            AsyncMock(
                return_value=SimpleNamespace(
                    in_app_enabled=True,
                    category_preferences={"ANNOUNCEMENT": True},
                )
            ),
        ),
        patch.object(
            svc,
            "get_default_category_preferences",
            AsyncMock(return_value={"ANNOUNCEMENT": True}),
        ),
        patch.object(
            svc,
            "list_personal_notifications_for_user",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            svc,
            "list_broadcast_notifications",
            AsyncMock(return_value=[announcement]),
        ),
        patch.object(
            svc,
            "get_campaign_audience_for_user",
            AsyncMock(return_value={campaign_id: audience}),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id)

    assert response.status is True
    assert response.data["items"][0]["is_read"] is True
    assert response.data["items"][0]["read_at"] is not None


@pytest.mark.asyncio
async def test_mark_as_read_broadcast_persists_campaign_audience(mock_db) -> None:
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    campaign_id = uuid4()
    notification_id = uuid4()
    read_at = datetime(2026, 2, 2, tzinfo=timezone.utc)
    broadcast = SimpleNamespace(
        id=notification_id,
        notification_type=SimpleNamespace(name="ANNOUNCEMENT"),
        notification_type_id=uuid4(),
        campaign_id=campaign_id,
        title="Campus news",
        body="hello",
        deep_link_payload={"broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    audience = SimpleNamespace(
        campaign_id=campaign_id,
        is_read=True,
        read_at=read_at,
    )

    with (
        patch.object(
            svc,
            "get_notification_for_user",
            AsyncMock(return_value=None),
        ),
        patch.object(
            svc,
            "get_broadcast_notification_by_id",
            AsyncMock(return_value=broadcast),
        ),
        patch.object(
            svc,
            "_user_topic_set",
            AsyncMock(return_value=set()),
        ),
        patch.object(
            svc,
            "mark_campaign_audience_read",
            AsyncMock(return_value=audience),
        ) as mark_audience,
    ):
        response = await svc.mark_as_read(
            db,
            user_id=user_id,
            notification_id=notification_id,
        )

    mark_audience.assert_awaited_once_with(
        db,
        user_id=user_id,
        campaign_id=campaign_id,
    )
    db.commit.assert_awaited_once()
    assert response.status is True
    assert response.data.is_read is True


@pytest.mark.asyncio
async def test_mark_all_read_includes_broadcast_campaign_audience(mock_db) -> None:
    from apps.notifications.services import notification_service as svc

    db = mock_db()
    user_id = uuid4()
    campaign_id = uuid4()
    broadcast = SimpleNamespace(
        id=uuid4(),
        notification_type=SimpleNamespace(name="ANNOUNCEMENT"),
        notification_type_id=uuid4(),
        campaign_id=campaign_id,
        title="Campus news",
        body="hello",
        deep_link_payload={"broadcast": True},
        is_read=False,
        read_at=None,
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    with (
        patch.object(
            svc,
            "persist_mark_all_read",
            AsyncMock(return_value=2),
        ),
        patch.object(
            svc,
            "_list_unified_notifications_for_user",
            AsyncMock(return_value=[(broadcast, True, False, None)]),
        ),
        patch.object(
            svc,
            "mark_all_campaign_audience_read",
            AsyncMock(return_value=1),
        ) as mark_all_audience,
    ):
        response = await svc.mark_all_read(db, user_id=user_id)

    mark_all_audience.assert_awaited_once_with(
        db,
        user_id=user_id,
        campaign_ids=[campaign_id],
    )
    db.commit.assert_awaited_once()
    assert response.status is True
    assert response.data["updated_count"] == 3


@pytest.mark.asyncio
async def test_create_campaign_with_to_all_target(mock_db) -> None:
    from apps.notifications.schemas import CreateCampaignRequest
    from common.enums import NotificationTargetType

    db = mock_db()
    admin_id = uuid4()
    payload = CreateCampaignRequest(
        title="All Universities Announcement",
        message="Important news for all students",
        campaign_type=NotificationCampaignType.topic,
        targets=[
            {
                "type": NotificationTargetType.university,
                "to_all": True,
                "values": [],
            }
        ],
    )

    created_campaign = _campaign(
        title="All Universities Announcement",
        message="Important news for all students",
        campaign_type=NotificationCampaignType.topic,
    )

    with (
        patch.object(
            admin_svc,
            "get_notification_type_by_name",
            AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ),
        patch.object(
            admin_svc,
            "persist_campaign",
            AsyncMock(return_value=created_campaign),
        ) as persist,
        patch.object(
            admin_svc,
            "resolve_topic_recipients",
            AsyncMock(return_value=[uuid4()]),
        ),
        patch.object(
            admin_svc,
            "_dispatch_topic",
            AsyncMock(),
        ),
        patch(
            "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        response = await admin_svc.create_campaign(
            db,
            admin_user_id=admin_id,
            payload=payload,
            actor_role="superadmin",
        )

    assert response.status is True
    persist.assert_awaited_once()
    activity.assert_not_awaited()
    stored_targets = persist.await_args.kwargs["deep_link_payload"]["targets"]
    assert stored_targets == [
        {"type": "UNIVERSITY", "to_all": True, "values": []}
    ]


@pytest.mark.asyncio
async def test_create_campaign_persists_users_is_alumni_target(mock_db) -> None:
    from apps.notifications.schemas import CreateCampaignRequest
    from common.enums import NotificationTargetType

    db = mock_db()
    payload = CreateCampaignRequest(
        title="test notification",
        message="test notification message",
        campaign_type=NotificationCampaignType.topic,
        targets=[
            {"type": NotificationTargetType.university, "values": [], "to_all": True},
            {"type": NotificationTargetType.users, "values": [], "to_all": True, "is_alumni": True},
        ],
    )
    created_campaign = _campaign(
        title="test notification",
        message="test notification message",
        campaign_type=NotificationCampaignType.topic,
    )

    with (
        patch.object(
            admin_svc,
            "get_notification_type_by_name",
            AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ),
        patch.object(
            admin_svc,
            "persist_campaign",
            AsyncMock(return_value=created_campaign),
        ) as persist,
        patch.object(
            admin_svc,
            "resolve_topic_recipients",
            AsyncMock(return_value=[uuid4()]),
        ) as resolve,
    ):
        response = await admin_svc.create_campaign(
            db,
            admin_user_id=uuid4(),
            payload=payload,
        )

    assert response.status is True
    stored_targets = persist.await_args.kwargs["deep_link_payload"]["targets"]
    assert stored_targets == [
        {"type": "UNIVERSITY", "to_all": True, "values": []},
        {"type": "USERS", "to_all": True, "values": [], "is_alumni": True},
    ]
    resolve.assert_awaited_once()
    resolved_targets = resolve.await_args.kwargs["targets"]
    assert resolved_targets[-1] == (
        NotificationTargetType.users,
        [],
        True,
        True,
    )


@pytest.mark.asyncio
async def test_create_topic_campaign_rejects_when_no_eligible_users(mock_db) -> None:
    from apps.notifications.schemas import CreateCampaignRequest
    from common.enums import NotificationTargetType

    db = mock_db()
    payload = CreateCampaignRequest(
        title="test pN2",
        message="test pN2 message",
        campaign_type=NotificationCampaignType.topic,
        targets=[
            {
                "type": NotificationTargetType.country,
                "to_all": False,
                "values": ["60f95ac1-53a3-5e4b-8e37-9def12eb97bd"],
            }
        ],
    )

    with (
        patch.object(
            admin_svc,
            "get_notification_type_by_name",
            AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ),
        patch.object(
            admin_svc,
            "resolve_topic_recipients",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            admin_svc,
            "persist_campaign",
            AsyncMock(),
        ) as persist,
    ):
        response = await admin_svc.create_campaign(
            db,
            admin_user_id=uuid4(),
            payload=payload,
            actor_role="superadmin",
        )

    assert response.status is False
    assert response.message == "No eligible users"
    assert response.data is None
    persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_announcement_campaign_rejects_when_no_eligible_users(mock_db) -> None:
    from apps.notifications.schemas import CreateCampaignRequest

    db = mock_db()
    payload = CreateCampaignRequest(
        title="Campus news",
        message="Hello",
        campaign_type=NotificationCampaignType.announcement,
        targets=[],
    )

    with (
        patch.object(
            admin_svc,
            "get_notification_type_by_name",
            AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ),
        patch.object(
            admin_svc,
            "resolve_announcement_recipients",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            admin_svc,
            "persist_campaign",
            AsyncMock(),
        ) as persist,
    ):
        response = await admin_svc.create_campaign(
            db,
            admin_user_id=uuid4(),
            payload=payload,
        )

    assert response.status is False
    assert response.message == "No eligible users"
    persist.assert_not_awaited()


def test_create_campaign_target_validation_to_all() -> None:
    from apps.notifications.schemas import CreateCampaignTarget
    from common.enums import NotificationTargetType
    import pydantic

    # When to_all is True, values can be empty
    target_to_all = CreateCampaignTarget(
        type=NotificationTargetType.university,
        to_all=True,
        values=[],
    )
    assert target_to_all.to_all is True
    assert target_to_all.values == []

    # When to_all is False, values cannot be empty
    with pytest.raises(pydantic.ValidationError):
        CreateCampaignTarget(
            type=NotificationTargetType.university,
            to_all=False,
            values=[],
        )


def test_create_campaign_target_users_is_alumni_with_to_all() -> None:
    from apps.notifications.schemas import (
        CampaignTargetResponse,
        CreateCampaignRequest,
        CreateCampaignTarget,
    )
    from common.enums import NotificationTargetType

    target = CreateCampaignTarget(
        type=NotificationTargetType.users,
        to_all=True,
        values=[],
        is_alumni=True,
    )
    assert target.to_all is True
    assert target.is_alumni is True
    assert target.values == []

    dumped = CampaignTargetResponse(
        type=NotificationTargetType.users,
        to_all=True,
        values=[],
        is_alumni=True,
    ).model_dump(mode="json")
    assert dumped == {
        "type": "USERS",
        "to_all": True,
        "values": [],
        "is_alumni": True,
    }

    omitted = CampaignTargetResponse(
        type=NotificationTargetType.university,
        to_all=True,
        values=[],
    ).model_dump(mode="json")
    assert "is_alumni" not in omitted

    payload = CreateCampaignRequest(
        title="test notification",
        message="test notification message",
        campaign_type="TOPIC",
        targets=[
            {"type": "UNIVERSITY", "values": [], "to_all": True},
            {"type": "MAJOR", "values": [], "to_all": True},
            {"type": "MINOR", "values": [], "to_all": True},
            {"type": "EDUCATION_LEVEL", "values": [], "to_all": True},
            {"type": "COUNTRY", "values": [], "to_all": True},
            {"type": "INTERESTS", "values": [], "to_all": True},
            {"type": "USERS", "values": [], "to_all": True, "is_alumni": True},
        ],
    )
    users_target = payload.targets[-1]
    assert users_target.type == NotificationTargetType.users
    assert users_target.to_all is True
    assert users_target.is_alumni is True


@pytest.mark.asyncio
async def test_dispatch_logs_sent_activity_after_success(mock_db) -> None:
    db = mock_db()
    campaign = _campaign(campaign_type=NotificationCampaignType.announcement)

    with (
        patch.object(admin_svc, "get_campaign_for_dispatch", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "get_notification_by_campaign_id",
            AsyncMock(return_value=None),
        ),
        patch.object(admin_svc, "_dispatch_announcement", AsyncMock()),
        patch.object(
            admin_svc,
            "_update_campaign_status",
            AsyncMock(return_value=campaign),
        ) as update_status,
        patch.object(
            admin_svc,
            "_log_campaign_dispatch_activity",
            AsyncMock(),
        ) as activity_log,
    ):
        await admin_svc.dispatch_campaign(
            db,
            campaign.id,
            actor_role="superadmin",
        )

    assert update_status.await_args.kwargs["status"] == NotificationCampaignStatus.sent
    activity_log.assert_awaited_once()
    assert activity_log.await_args.args[1] is campaign
    assert activity_log.await_args.kwargs["status"] == NotificationCampaignStatus.sent
    assert activity_log.await_args.kwargs["actor_role"] == "superadmin"


@pytest.mark.asyncio
async def test_dispatch_logs_failed_activity_when_send_raises(mock_db) -> None:
    db = mock_db()
    campaign = _campaign(campaign_type=NotificationCampaignType.topic)

    with (
        patch.object(admin_svc, "get_campaign_for_dispatch", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "get_notification_by_campaign_id",
            AsyncMock(return_value=None),
        ),
        patch.object(admin_svc, "_get_campaign", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "_dispatch_topic",
            AsyncMock(side_effect=RuntimeError("push down")),
        ),
        patch.object(
            admin_svc,
            "_update_campaign_status",
            AsyncMock(return_value=campaign),
        ) as update_status,
        patch.object(
            admin_svc,
            "_log_campaign_dispatch_activity",
            AsyncMock(),
        ) as activity_log,
    ):
        with pytest.raises(RuntimeError, match="push down"):
            await admin_svc.dispatch_campaign(
                db,
                campaign.id,
                actor_role="superadmin",
            )

    assert update_status.await_args.kwargs["status"] == NotificationCampaignStatus.failed
    activity_log.assert_awaited_once()
    assert activity_log.await_args.kwargs["status"] == NotificationCampaignStatus.failed
    assert activity_log.await_args.kwargs["actor_role"] == "superadmin"


@pytest.mark.asyncio
async def test_log_campaign_dispatch_activity_uses_sent_status() -> None:
    db = AsyncMock()
    campaign = _campaign(
        campaign_type=NotificationCampaignType.announcement,
        title="Campus news",
    )

    with patch(
        "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
        AsyncMock(),
    ) as activity:
        await admin_svc._log_campaign_dispatch_activity(
            db,
            campaign,
            status=NotificationCampaignStatus.sent,
            actor_role="superadmin",
        )

    activity.assert_awaited_once()
    kwargs = activity.await_args.kwargs
    assert kwargs["action"] == "create"
    assert kwargs["module"] == "notification_campaign"
    assert kwargs["description"] == "sent the announcement"
    assert kwargs["metadata"]["new"]["status"] == "SENT"
    assert kwargs["metadata"]["new"]["title"] == "Campus news"


@pytest.mark.asyncio
async def test_log_campaign_dispatch_activity_uses_failed_status() -> None:
    db = AsyncMock()
    campaign = _campaign(
        campaign_type=NotificationCampaignType.topic,
        title="Topic news",
    )

    with patch(
        "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
        AsyncMock(),
    ) as activity:
        await admin_svc._log_campaign_dispatch_activity(
            db,
            campaign,
            status=NotificationCampaignStatus.failed,
            actor_role="superadmin",
        )

    kwargs = activity.await_args.kwargs
    assert kwargs["action"] == "fail"
    assert kwargs["description"] == "failed to send a topic based notification"
    assert kwargs["metadata"]["new"]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_dispatch_skips_non_draft_campaign(mock_db) -> None:
    db = mock_db()
    campaign = _campaign(status=NotificationCampaignStatus.sent)

    with (
        patch.object(admin_svc, "get_campaign_for_dispatch", AsyncMock(return_value=campaign)),
        patch.object(admin_svc, "_dispatch_announcement", AsyncMock()) as dispatch,
        patch.object(admin_svc, "_dispatch_topic", AsyncMock()) as dispatch_topic,
    ):
        await admin_svc.dispatch_campaign(db, campaign.id)

    dispatch.assert_not_awaited()
    dispatch_topic.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_skips_when_broadcast_notification_already_exists(mock_db) -> None:
    db = mock_db()
    campaign = _campaign()
    existing = SimpleNamespace(id=uuid4())

    with (
        patch.object(admin_svc, "get_campaign_for_dispatch", AsyncMock(return_value=campaign)),
        patch.object(
            admin_svc,
            "get_notification_by_campaign_id",
            AsyncMock(return_value=existing),
        ),
        patch.object(
            admin_svc,
            "_update_campaign_status",
            AsyncMock(return_value=campaign),
        ) as update_status,
        patch.object(admin_svc, "_dispatch_announcement", AsyncMock()) as dispatch,
    ):
        await admin_svc.dispatch_campaign(db, campaign.id)

    dispatch.assert_not_awaited()
    update_status.assert_awaited_once()
    assert update_status.await_args.kwargs["status"] == NotificationCampaignStatus.sent
    db.commit.assert_awaited_once()


