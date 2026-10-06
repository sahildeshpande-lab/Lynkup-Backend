from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.notifications.services.notification_service import (
    POST_ASSIGNED_MODERATOR,
    POST_ASSIGNED_SUPERADMIN,
    POST_UPDATED,
    notify_post_assigned,
    notify_post_edited,
    _resolve_user_full_name,
)


@pytest.mark.asyncio
async def test_notify_post_assigned():
    db = AsyncMock()
    post_id = uuid4()
    author_id = uuid4()
    moderator_id = uuid4()
    superadmin_id = uuid4()

    async def _mock_resolve_name(_db, uid):
        if uid == author_id:
            return "Sahil Deshpande"
        if uid == moderator_id:
            return "Prasad Pawar"
        return "Admin"

    with (
        patch(
            "apps.notifications.services.notification_service._resolve_user_full_name",
            side_effect=_mock_resolve_name,
        ),
        patch(
            "apps.notifications.services.notification_service._fetch_superadmin_user_ids",
            AsyncMock(return_value=[superadmin_id]),
        ),
        patch(
            "apps.notifications.services.notification_service._ensure_notification_type",
            AsyncMock(),
        ),
        patch(
            "apps.notifications.services.notification_service.create_notification",
            AsyncMock(),
        ) as create_mock,
        patch(
            "apps.administration.repositories.admin_activity_log_repository.create_admin_activity_log_record",
            AsyncMock(),
        ) as log_mock,
    ):
        await notify_post_assigned(
            db,
            post_id=post_id,
            author_user_id=author_id,
            moderator_id=moderator_id,
        )

        assert create_mock.call_count == 2

        # First call -> assigned moderator
        mod_call = create_mock.call_args_list[0]
        assert mod_call.kwargs["recipient_user_id"] == moderator_id
        assert mod_call.kwargs["notification_type"] == POST_ASSIGNED_MODERATOR
        assert mod_call.kwargs["title"] == "New Post Assigned - Moderator"
        assert (
            mod_call.kwargs["body"]
            == "Sahil Deshpande created a new post and it has been assigned to you for moderation."
        )

        # Second call -> superadmin
        sa_call = create_mock.call_args_list[1]
        assert sa_call.kwargs["recipient_user_id"] == superadmin_id
        assert sa_call.kwargs["notification_type"] == POST_ASSIGNED_SUPERADMIN
        assert sa_call.kwargs["title"] == "New Post Assigned - Superadmin"
        assert (
            sa_call.kwargs["body"]
            == "Sahil Deshpande created a new post and it has been assigned to Prasad Pawar for moderation."
        )

        # Activity log record created
        assert log_mock.call_count == 1
        assert log_mock.call_args.kwargs["role"] == "superadmin"
        assert log_mock.call_args.kwargs["action"] == "assign"
        assert log_mock.call_args.kwargs["module"] == "post"
        assert log_mock.call_args.kwargs["record_id"] == post_id
        assert "Sahil Deshpande created a new post" in log_mock.call_args.kwargs["description"]


@pytest.mark.asyncio
async def test_notify_post_edited():
    db = AsyncMock()
    post_id = uuid4()
    author_id = uuid4()
    moderator_id = uuid4()
    superadmin_id = uuid4()

    with (
        patch(
            "apps.notifications.services.notification_service._resolve_user_full_name",
            AsyncMock(return_value="Sahil Deshpande"),
        ),
        patch(
            "apps.notifications.services.notification_service._fetch_superadmin_user_ids",
            AsyncMock(return_value=[superadmin_id]),
        ),
        patch(
            "apps.notifications.services.notification_service._ensure_notification_type",
            AsyncMock(),
        ),
        patch(
            "apps.notifications.services.notification_service.create_notification",
            AsyncMock(),
        ) as create_mock,
        patch(
            "apps.administration.repositories.admin_activity_log_repository.create_admin_activity_log_record",
            AsyncMock(),
        ) as log_mock,
    ):
        await notify_post_edited(
            db,
            post_id=post_id,
            author_user_id=author_id,
            moderator_id=moderator_id,
        )

        assert create_mock.call_count == 2
        recipients = {call.kwargs["recipient_user_id"] for call in create_mock.call_args_list}
        assert recipients == {moderator_id, superadmin_id}

        for call in create_mock.call_args_list:
            assert call.kwargs["notification_type"] == POST_UPDATED
            assert call.kwargs["title"] == "Post Updated"
            assert call.kwargs["body"] == "Sahil Deshpande Updated the post"

        # Activity log record created
        assert log_mock.call_count == 1
        assert log_mock.call_args.kwargs["role"] == "superadmin"
        assert log_mock.call_args.kwargs["action"] == "update"
        assert log_mock.call_args.kwargs["module"] == "post"
        assert log_mock.call_args.kwargs["record_id"] == post_id
        assert log_mock.call_args.kwargs["description"] == "Sahil Deshpande Updated the post"


@pytest.mark.asyncio
async def test_resolve_user_full_name_with_profile():
    db = AsyncMock()
    user = MagicMock(id=uuid4(), email="sahil@example.com")
    profile = MagicMock(first_name="Sahil", last_name="Deshpande")

    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = (user, profile)
    db.execute.return_value = mock_exec_result

    name = await _resolve_user_full_name(db, user.id)
    assert name == "Sahil Deshpande"


@pytest.mark.asyncio
async def test_resolve_user_full_name_fallback_email():
    db = AsyncMock()
    user = MagicMock(id=uuid4(), email="prasad@example.com")
    profile = MagicMock(first_name=None, last_name=None, username=None)

    mock_exec_result = MagicMock()
    mock_exec_result.first.return_value = (user, profile)
    db.execute.return_value = mock_exec_result

    name = await _resolve_user_full_name(db, user.id)
    assert name == "prasad@example.com"
