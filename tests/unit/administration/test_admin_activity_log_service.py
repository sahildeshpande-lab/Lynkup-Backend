from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.administration.db_models.admin_activity_log_db_model import AdminActivityLog
from apps.administration.services.admin_activity_log_service import (
    create_admin_activity_log,
    format_field_changes,
    format_post_moderation_description,
    json_safe,
    list_admin_activity_logs_service,
    serialize_admin_activity_log,
)


def _log(**overrides) -> AdminActivityLog:
    now = datetime.now(timezone.utc)
    data = {
        "id": uuid4(),
        "user_id": uuid4(),
        "role": "moderator",
        "action": "reject",
        "module": "post",
        "record_id": uuid4(),
        "description": "Post rejected during moderation",
        "log_metadata": {"old": {"status": "under_review"}, "new": {"status": "rejected"}},
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return AdminActivityLog(**data)


@pytest.mark.asyncio
async def test_superadmin_action_creates_log(mock_db):
    db = mock_db()
    user_id = uuid4()
    record_id = uuid4()

    result = await create_admin_activity_log(
        db,
        user_id=user_id,
        role="superadmin",
        action="create",
        module="user",
        record_id=record_id,
        description="Created moderator account",
        metadata={"old": None, "new": {"role": "moderator"}},
    )

    assert result is not None
    assert result.user_id == user_id
    assert result.role == "superadmin"
    assert result.action == "create"
    assert result.module == "user"
    assert result.record_id == record_id
    assert result.log_metadata["new"]["role"] == "moderator"
    db.add.assert_called_once()
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_superadmin_actor_label_includes_first_name(mock_db, scalar_result):
    from apps.administration.services.admin_activity_log_service import activity_actor_label

    db = mock_db(scalar_result(values=[("Ada",)]))
    label = await activity_actor_label(db, user_id=uuid4(), role="superadmin")
    assert label == "Super Admin Ada"


@pytest.mark.asyncio
async def test_description_prefixes_superadmin_actor(mock_db):
    db = mock_db()
    result = await create_admin_activity_log(
        db,
        user_id=uuid4(),
        role="superadmin",
        action="create",
        module="user",
        description="created shildeshpande@yopmail.com",
    )
    assert result is not None
    assert result.description == "Super Admin created shildeshpande@yopmail.com"


def test_format_post_moderation_description_includes_author_name():
    assert (
        format_post_moderation_description("published", "Jane Doe")
        == "published the Jane Doe post"
    )
    assert (
        format_post_moderation_description("flagged", "Jane Doe")
        == "flagged the Jane Doe post"
    )
    assert (
        format_post_moderation_description("reinstate", "Jane Doe")
        == "reinstate the Jane Doe post"
    )
    assert (
        format_post_moderation_description("rejected", "Jane Doe")
        == "rejected the Jane Doe post"
    )


def test_format_post_moderation_description_falls_back_without_author():
    assert format_post_moderation_description("published", None) == "published a post"
    assert format_post_moderation_description("escalate", "Jane Doe") == "escalate a post"


@pytest.mark.asyncio
async def test_description_prefixes_superadmin_on_post_moderation(mock_db):
    db = mock_db()
    result = await create_admin_activity_log(
        db,
        user_id=uuid4(),
        role="superadmin",
        action="published",
        module="post",
        description=format_post_moderation_description("published", "Jane Doe"),
    )
    assert result is not None
    assert result.description == "Super Admin published the Jane Doe post"


@pytest.mark.asyncio
async def test_description_prefixes_moderator_name_on_post_moderation(mock_db):
    db = mock_db()
    with patch(
        "apps.administration.services.admin_activity_log_service.activity_actor_label",
        AsyncMock(return_value="Ada Moderator"),
    ):
        result = await create_admin_activity_log(
            db,
            user_id=uuid4(),
            role="moderator",
            action="flagged",
            module="post",
            description=format_post_moderation_description("flagged", "Jane Doe"),
        )
    assert result is not None
    assert result.description == "Ada Moderator flagged the Jane Doe post"


def test_format_field_changes_for_recommendation_toggle():
    text = format_field_changes(
        {"is_enabled": True, "generation_frequency_days": 14},
        {"is_enabled": False, "generation_frequency_days": 14},
    )
    assert text == "disabled the learning spotlight feature"


def test_format_field_changes_for_learning_spotlight_papers_count():
    text = format_field_changes(
        {"learning_spotlight_papers_count": 1},
        {"learning_spotlight_papers_count": 2},
    )
    assert text == "learning spotlight paper count to 2"


def test_format_field_changes_for_is_pushnotification_enabled():
    disabled = format_field_changes(
        {"is_pushnotification_enabled": True},
        {"is_pushnotification_enabled": False},
    )
    enabled = format_field_changes(
        {"is_pushnotification_enabled": False},
        {"is_pushnotification_enabled": True},
    )
    assert disabled == "disabled learning spotlight push notifications"
    assert enabled == "enabled learning spotlight push notifications"


@pytest.mark.asyncio
async def test_moderator_action_creates_log(mock_db):
    db = mock_db()
    result = await create_admin_activity_log(
        db,
        user_id=uuid4(),
        role="moderator",
        action="suspend",
        module="user",
        record_id=uuid4(),
        description="User status changed to suspended",
        metadata={"old": {"status": "active"}, "new": {"status": "suspended"}},
    )

    assert result is not None
    assert result.role == "moderator"
    assert result.log_metadata["old"]["status"] == "active"
    assert result.log_metadata["new"]["status"] == "suspended"
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_viewer_action_does_not_create_log(mock_db):
    db = mock_db()
    result = await create_admin_activity_log(
        db,
        user_id=uuid4(),
        role="viewer",
        action="update",
        module="feature_flag",
        record_id=uuid4(),
        description="Feature flag updated",
        metadata={"old": {"enabled": True}, "new": {"enabled": False}},
    )

    assert result is None
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_regular_user_does_not_create_log(mock_db):
    db = mock_db()
    result = await create_admin_activity_log(
        db,
        user_id=uuid4(),
        role="user",
        action="create",
        module="user",
    )

    assert result is None
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_system_and_cron_roles_do_not_create_log(mock_db):
    db = mock_db()
    for role in ("system", "cron", None, ""):
        result = await create_admin_activity_log(
            db,
            user_id=uuid4(),
            role=role,
            action="run",
            module="recommendation",
        )
        assert result is None
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_nullable_record_id_works(mock_db):
    db = mock_db()
    result = await create_admin_activity_log(
        db,
        user_id=uuid4(),
        role="superadmin",
        action="update",
        module="recommendation_settings",
        record_id=None,
        description="Recommendation settings updated",
        metadata={"old": {"is_enabled": True}, "new": {"is_enabled": False}},
    )

    assert result is not None
    assert result.record_id is None


@pytest.mark.asyncio
async def test_logging_does_not_commit_so_failed_operations_roll_back(mock_db):
    db = mock_db()
    db.commit = AsyncMock(side_effect=RuntimeError("business commit failed"))
    db.rollback = AsyncMock()

    result = await create_admin_activity_log(
        db,
        user_id=uuid4(),
        role="moderator",
        action="rejected",
        module="post",
        record_id=uuid4(),
    )
    assert result is not None
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()

    with pytest.raises(RuntimeError, match="business commit failed"):
        await db.commit()
    await db.rollback()
    db.rollback.assert_awaited_once()


def test_json_safe_converts_uuid_and_enum():
    from common.enums import UserStatus

    record_id = uuid4()
    payload = json_safe(
        {
            "id": record_id,
            "status": UserStatus.suspended,
            "nested": [record_id],
        }
    )
    assert payload["id"] == str(record_id)
    assert payload["status"] == "suspended"
    assert payload["nested"] == [str(record_id)]


@pytest.mark.asyncio
async def test_list_returns_all_items_when_pagination_absent():
    rows = [(_log(), "Ada Moderator"), (_log(), "Sam Superadmin")]
    db = AsyncMock()
    with patch(
        "apps.administration.services.admin_activity_log_service.fetch_admin_activity_logs",
        AsyncMock(return_value=(rows, 2)),
    ) as fetch:
        data = await list_admin_activity_logs_service(db, search="post")

    assert "page" not in data
    assert len(data["items"]) == 2
    assert data["items"][0]["user_name"] == "Ada Moderator"
    assert data["items"][1]["user_name"] == "Sam Superadmin"
    fetch.assert_awaited_once()
    assert fetch.await_args.kwargs["limit"] is None
    assert fetch.await_args.kwargs["search"] == "post"
    assert fetch.await_args.kwargs["module"] is None
    from common.enums import AdminActivityLogOrder, AdminActivityLogSort

    assert fetch.await_args.kwargs["sort"] == AdminActivityLogSort.created_at
    assert fetch.await_args.kwargs["order"] == AdminActivityLogOrder.desc


@pytest.mark.asyncio
async def test_list_uses_common_pagination_when_supplied():
    rows = [(_log(), "Ada Moderator")]
    db = AsyncMock()
    with patch(
        "apps.administration.services.admin_activity_log_service.fetch_admin_activity_logs",
        AsyncMock(return_value=(rows, 21)),
    ) as fetch:
        data = await list_admin_activity_logs_service(
            db,
            page=2,
            page_size=10,
        )

    assert data["page"] == 2
    assert data["pageSize"] == 10
    assert data["totalItems"] == 21
    assert data["totalPages"] == 3
    assert fetch.await_args.kwargs["offset"] == 10
    assert fetch.await_args.kwargs["limit"] == 10
    from common.enums import AdminActivityLogOrder, AdminActivityLogSort

    assert fetch.await_args.kwargs["sort"] == AdminActivityLogSort.created_at
    assert fetch.await_args.kwargs["order"] == AdminActivityLogOrder.desc


@pytest.mark.asyncio
async def test_list_empty_results_follow_api_conventions():
    db = AsyncMock()
    with patch(
        "apps.administration.services.admin_activity_log_service.fetch_admin_activity_logs",
        AsyncMock(return_value=([], 0)),
    ):
        data = await list_admin_activity_logs_service(db)

    assert data == {"items": []}


@pytest.mark.asyncio
async def test_list_passes_module_sort_and_order():
    from common.enums import AdminActivityLogOrder, AdminActivityLogSort

    db = AsyncMock()
    with patch(
        "apps.administration.services.admin_activity_log_service.fetch_admin_activity_logs",
        AsyncMock(return_value=([], 0)),
    ) as fetch:
        data = await list_admin_activity_logs_service(
            db,
            module="report",
            role="superadmin",
            sort=AdminActivityLogSort.module,
            order=AdminActivityLogOrder.asc,
        )

    assert data == {"items": []}
    assert fetch.await_args.kwargs["module"] == "report"
    assert fetch.await_args.kwargs["role"] == "superadmin"
    assert fetch.await_args.kwargs["sort"] == AdminActivityLogSort.module
    assert fetch.await_args.kwargs["order"] == AdminActivityLogOrder.asc


def test_serialize_admin_activity_log_exposes_metadata_key():
    row = _log()
    payload = serialize_admin_activity_log(row, user_name="Ada Moderator")
    assert payload["metadata"] == row.log_metadata
    assert payload["user_id"] == row.user_id
    assert payload["user_name"] == "Ada Moderator"
    assert "log_metadata" not in payload


def test_serialize_admin_activity_log_adds_country_details():
    country_id = uuid4()
    row = _log(
        module="profile",
        log_metadata={
            "old": {
                "first_name": "Sahil",
                "country_id": str(country_id),
            },
            "new": {
                "first_name": "Avinash",
                "country_id": str(country_id),
            },
        },
    )
    payload = serialize_admin_activity_log(
        row,
        country_names={str(country_id): "United States"},
    )
    assert payload["metadata"]["old"]["country_details"] == {
        "id": str(country_id),
        "country_name": "United States",
    }
    assert payload["metadata"]["new"]["country_details"] == {
        "id": str(country_id),
        "country_name": "United States",
    }


@pytest.mark.asyncio
async def test_list_enriches_profile_country_details_from_country_id():
    country_id = uuid4()
    row = _log(
        module="profile",
        log_metadata={
            "old": {"country_id": str(country_id), "first_name": "Sahil"},
            "new": {"country_id": str(country_id), "first_name": "Avinash"},
        },
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=type(
            "_Rows",
            (),
            {"all": lambda self: [(country_id, "United States")]},
        )()
    )
    with patch(
        "apps.administration.services.admin_activity_log_service.fetch_admin_activity_logs",
        AsyncMock(return_value=([(row, "Super Admin")], 1)),
    ):
        data = await list_admin_activity_logs_service(db, module="profile")

    details = data["items"][0]["metadata"]["new"]["country_details"]
    assert details == {"id": str(country_id), "country_name": "United States"}
    assert data["items"][0]["metadata"]["old"]["country_id"] == str(country_id)


@pytest.mark.asyncio
async def test_list_applies_moderator_id_filter():
    db = AsyncMock()
    mod_id = uuid4()
    with patch(
        "apps.administration.services.admin_activity_log_service.fetch_admin_activity_logs",
        AsyncMock(return_value=([], 0)),
    ) as fetch:
        data = await list_admin_activity_logs_service(
            db,
            moderator_id=mod_id,
        )

    assert data == {"items": []}
    assert fetch.await_args.kwargs["moderator_id"] == mod_id

