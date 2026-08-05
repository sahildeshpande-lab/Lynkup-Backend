from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.notifications.repositories import notification_repository as repo


def _preference(user_id, **kwargs):
    defaults = {
        "user_id": user_id,
        "push_enabled": True,
        "in_app_enabled": True,
        "category_preferences": {
            "ANNOUNCEMENT": True,
            "TOPIC": True,
        },
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


ACTIVE_DEFAULTS = {
    "CONNECTION_REQUEST": True,
    "ANNOUNCEMENT": True,
    "TOPIC": True,
}


@pytest.mark.asyncio
async def test_filter_users_eligible_for_push_includes_users_without_preferences(mock_db):
    db = mock_db()
    user_ids = [uuid4(), uuid4()]

    with patch.object(
        repo,
        "get_default_category_preferences",
        AsyncMock(return_value=dict(ACTIVE_DEFAULTS)),
    ):
        eligible = await repo.filter_users_eligible_for_push(
            db,
            user_ids,
            category="ANNOUNCEMENT",
        )

    assert eligible == user_ids


@pytest.mark.asyncio
async def test_filter_users_eligible_for_push_excludes_push_disabled(mock_db, scalar_result):
    user_id = uuid4()
    db = mock_db(
        scalar_result(
            values=[
                _preference(
                    user_id,
                    push_enabled=False,
                    category_preferences={"ANNOUNCEMENT": True},
                )
            ]
        )
    )

    with patch.object(
        repo,
        "get_default_category_preferences",
        AsyncMock(return_value=dict(ACTIVE_DEFAULTS)),
    ):
        eligible = await repo.filter_users_eligible_for_push(
            db,
            [user_id],
            category="ANNOUNCEMENT",
        )

    assert eligible == []


@pytest.mark.asyncio
async def test_filter_users_eligible_for_push_excludes_disabled_category(mock_db, scalar_result):
    user_id = uuid4()
    db = mock_db(
        scalar_result(
            values=[
                _preference(
                    user_id,
                    push_enabled=True,
                    category_preferences={"ANNOUNCEMENT": False, "TOPIC": True},
                )
            ]
        )
    )

    with patch.object(
        repo,
        "get_default_category_preferences",
        AsyncMock(return_value=dict(ACTIVE_DEFAULTS)),
    ):
        announcement_eligible = await repo.filter_users_eligible_for_push(
            db,
            [user_id],
            category="ANNOUNCEMENT",
        )
        topic_eligible = await repo.filter_users_eligible_for_push(
            db,
            [user_id],
            category="TOPIC",
        )

    assert announcement_eligible == []
    assert topic_eligible == [user_id]


@pytest.mark.asyncio
async def test_filter_users_eligible_for_push_allows_in_app_only_users(mock_db, scalar_result):
    """push_enabled=false with category on — no push, but in-app remains unaffected."""
    user_id = uuid4()
    db = mock_db(
        scalar_result(
            values=[
                _preference(
                    user_id,
                    push_enabled=False,
                    in_app_enabled=True,
                    category_preferences={"ANNOUNCEMENT": True},
                )
            ]
        )
    )

    with patch.object(
        repo,
        "get_default_category_preferences",
        AsyncMock(return_value=dict(ACTIVE_DEFAULTS)),
    ):
        eligible = await repo.filter_users_eligible_for_push(
            db,
            [user_id],
            category="ANNOUNCEMENT",
        )

    assert eligible == []
