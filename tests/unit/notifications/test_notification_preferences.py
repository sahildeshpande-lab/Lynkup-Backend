from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.notifications.schemas import UpdateNotificationPreferencesRequest
from apps.notifications.services import notification_service as svc


def _preference(**kwargs):
    defaults = {
        "id": uuid4(),
        "user_id": uuid4(),
        "push_enabled": True,
        "in_app_enabled": True,
        "category_preferences": {
            "CONNECTION_REQUEST": True,
            "CONNECTION_ACCEPTED": True,
            "DIRECT_MESSAGE": True,
            "ANNOUNCEMENT": True,
            "TOPIC": True,
        },
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


ACTIVE_DEFAULTS = {
    "CONNECTION_REQUEST": True,
    "CONNECTION_ACCEPTED": True,
    "DIRECT_MESSAGE": True,
    "ANNOUNCEMENT": True,
    "TOPIC": True,
}


@pytest.mark.asyncio
async def test_merged_category_preferences_uses_db_defaults_and_user_overrides(mock_db):
    db = mock_db()
    existing = {
        "CONNECTION_REQUEST": False,
        "DIRECT_MESSAGE": False,
        "LEGACY_INACTIVE": False,
    }

    with patch.object(
        svc,
        "get_default_category_preferences",
        AsyncMock(return_value=dict(ACTIVE_DEFAULTS)),
    ):
        merged = await svc._merged_category_preferences(db, existing)

    assert merged == {
        "CONNECTION_REQUEST": False,
        "CONNECTION_ACCEPTED": True,
        "DIRECT_MESSAGE": False,
        "ANNOUNCEMENT": True,
        "TOPIC": True,
    }
    assert "LEGACY_INACTIVE" not in merged


@pytest.mark.asyncio
async def test_merged_category_preferences_excludes_deactivated_categories(mock_db):
    db = mock_db()
    existing = {
        "CONNECTION_REQUEST": False,
        "ANNOUNCEMENT": False,
        "TOPIC": True,
    }
    active_only = {
        "CONNECTION_REQUEST": True,
        "CONNECTION_ACCEPTED": True,
        "DIRECT_MESSAGE": True,
        # ANNOUNCEMENT deactivated — omitted from defaults
        "TOPIC": True,
    }

    with patch.object(
        svc,
        "get_default_category_preferences",
        AsyncMock(return_value=active_only),
    ):
        merged = await svc._merged_category_preferences(db, existing)

    assert "ANNOUNCEMENT" not in merged
    assert merged["CONNECTION_REQUEST"] is False
    assert merged["TOPIC"] is True


@pytest.mark.asyncio
async def test_get_preferences_initializes_new_user_from_db_categories(mock_db):
    db = mock_db()
    user_id = uuid4()
    created = _preference(user_id=user_id, category_preferences=dict(ACTIVE_DEFAULTS))

    with (
        patch.object(svc, "get_preferences_by_user_id", AsyncMock(return_value=None)),
        patch.object(
            svc,
            "get_default_category_preferences",
            AsyncMock(return_value=dict(ACTIVE_DEFAULTS)),
        ) as defaults,
        patch.object(svc, "create_preferences", AsyncMock(return_value=created)) as create,
    ):
        response = await svc.get_preferences(db, user_id=user_id)

    assert response.status is True
    assert response.message == "Notification preferences fetched successfully."
    assert response.data is not None
    assert response.data.category_preferences == ACTIVE_DEFAULTS
    defaults.assert_awaited()
    create.assert_awaited_once_with(
        db,
        user_id=user_id,
        category_preferences=ACTIVE_DEFAULTS,
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_preferences_existing_user_adds_new_category(mock_db):
    db = mock_db()
    user_id = uuid4()
    existing = _preference(
        user_id=user_id,
        category_preferences={
            "CONNECTION_REQUEST": False,
            "CONNECTION_ACCEPTED": True,
            "DIRECT_MESSAGE": True,
            "ANNOUNCEMENT": True,
            "TOPIC": True,
        },
    )
    defaults_with_new = {
        **ACTIVE_DEFAULTS,
        "EVENT_INVITE": True,
    }
    updated = _preference(
        user_id=user_id,
        category_preferences={
            "CONNECTION_REQUEST": False,
            "CONNECTION_ACCEPTED": True,
            "DIRECT_MESSAGE": True,
            "ANNOUNCEMENT": True,
            "TOPIC": True,
            "EVENT_INVITE": True,
        },
    )

    with (
        patch.object(svc, "get_preferences_by_user_id", AsyncMock(return_value=existing)),
        patch.object(
            svc,
            "get_default_category_preferences",
            AsyncMock(return_value=defaults_with_new),
        ),
        patch.object(
            svc,
            "persist_update_preferences",
            AsyncMock(return_value=updated),
        ) as persist,
    ):
        response = await svc.get_preferences(db, user_id=user_id)

    assert response.status is True
    assert response.data is not None
    assert response.data.category_preferences["EVENT_INVITE"] is True
    assert response.data.category_preferences["CONNECTION_REQUEST"] is False
    persist.assert_awaited_once()
    saved_prefs = persist.await_args.kwargs["category_preferences"]
    assert saved_prefs["EVENT_INVITE"] is True
    assert saved_prefs["CONNECTION_REQUEST"] is False


@pytest.mark.asyncio
async def test_get_preferences_excludes_deactivated_category(mock_db):
    db = mock_db()
    user_id = uuid4()
    existing = _preference(
        user_id=user_id,
        category_preferences={
            "CONNECTION_REQUEST": False,
            "TOPIC": True,
            "ANNOUNCEMENT": False,
        },
    )
    active_without_announcement = {
        "CONNECTION_REQUEST": True,
        "CONNECTION_ACCEPTED": True,
        "DIRECT_MESSAGE": True,
        "TOPIC": True,
    }
    updated = _preference(
        user_id=user_id,
        category_preferences={
            "CONNECTION_REQUEST": False,
            "CONNECTION_ACCEPTED": True,
            "DIRECT_MESSAGE": True,
            "TOPIC": True,
        },
    )

    with (
        patch.object(svc, "get_preferences_by_user_id", AsyncMock(return_value=existing)),
        patch.object(
            svc,
            "get_default_category_preferences",
            AsyncMock(return_value=active_without_announcement),
        ),
        patch.object(
            svc,
            "persist_update_preferences",
            AsyncMock(return_value=updated),
        ),
    ):
        response = await svc.get_preferences(db, user_id=user_id)

    assert response.data is not None
    assert "ANNOUNCEMENT" not in response.data.category_preferences
    assert response.data.category_preferences["CONNECTION_REQUEST"] is False


@pytest.mark.asyncio
async def test_update_preferences_partial_category_patch(mock_db):
    db = mock_db()
    user_id = uuid4()
    existing = _preference(
        user_id=user_id,
        push_enabled=True,
        in_app_enabled=True,
        category_preferences=dict(ACTIVE_DEFAULTS),
    )
    updated = _preference(
        user_id=user_id,
        push_enabled=False,
        in_app_enabled=True,
        category_preferences={
            **ACTIVE_DEFAULTS,
            "DIRECT_MESSAGE": False,
        },
    )
    payload = UpdateNotificationPreferencesRequest(
        push_enabled=False,
        category_preferences={"DIRECT_MESSAGE": False},
    )

    with (
        patch.object(svc, "get_preferences_by_user_id", AsyncMock(return_value=existing)),
        patch.object(
            svc,
            "get_default_category_preferences",
            AsyncMock(return_value=dict(ACTIVE_DEFAULTS)),
        ),
        patch.object(
            svc,
            "persist_update_preferences",
            AsyncMock(return_value=updated),
        ) as persist,
    ):
        response = await svc.update_preferences(db, user_id=user_id, payload=payload)

    assert response.status is True
    assert response.message == "Notification preferences updated successfully."
    assert response.data is not None
    assert response.data.push_enabled is False
    assert response.data.category_preferences["DIRECT_MESSAGE"] is False
    assert response.data.category_preferences["CONNECTION_REQUEST"] is True

    persist.assert_awaited_once()
    assert persist.await_args.kwargs["push_enabled"] is False
    assert persist.await_args.kwargs["in_app_enabled"] is None
    assert persist.await_args.kwargs["category_preferences"]["DIRECT_MESSAGE"] is False
    assert persist.await_args.kwargs["category_preferences"]["CONNECTION_REQUEST"] is True


@pytest.mark.asyncio
async def test_update_preferences_ignores_unknown_category_keys(mock_db):
    db = mock_db()
    user_id = uuid4()
    existing = _preference(user_id=user_id, category_preferences=dict(ACTIVE_DEFAULTS))
    payload = UpdateNotificationPreferencesRequest(
        category_preferences={"NOT_A_REAL_CATEGORY": False, "TOPIC": False},
    )

    with (
        patch.object(svc, "get_preferences_by_user_id", AsyncMock(return_value=existing)),
        patch.object(
            svc,
            "get_default_category_preferences",
            AsyncMock(side_effect=lambda _db: dict(ACTIVE_DEFAULTS)),
        ),
        patch.object(
            svc,
            "persist_update_preferences",
            AsyncMock(return_value=existing),
        ) as persist,
    ):
        await svc.update_preferences(db, user_id=user_id, payload=payload)

    saved = persist.await_args.kwargs["category_preferences"]
    assert "NOT_A_REAL_CATEGORY" not in saved
    assert saved["TOPIC"] is False


@pytest.mark.asyncio
async def test_get_default_category_preferences_falls_back_to_notification_types(
    mock_db,
    scalar_result,
):
    from apps.notifications.repositories import notification_repository as repo

    db = mock_db(
        # categories query returns empty
        scalar_result(values=[]),
        # types query returns active names
        scalar_result(values=["ANNOUNCEMENT", "TOPIC", "CONNECTION_REQUEST"]),
    )

    defaults = await repo.get_default_category_preferences(db)

    assert defaults == {
        "ANNOUNCEMENT": True,
        "TOPIC": True,
        "CONNECTION_REQUEST": True,
    }
