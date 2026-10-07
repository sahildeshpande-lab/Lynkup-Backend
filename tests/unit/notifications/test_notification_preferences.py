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
        "email_preferences": {
            "bulk_email": True,
        },
        "category_preferences": {
            "CONNECTION_REQUEST": True,
            "CONNECTION_ACCEPTED": True,
            "DIRECT_MESSAGE": True,
            "ANNOUNCEMENT": True,
            "TOPIC": True,
            "weekly_lynkup_request_reminder": True,
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

MERGED_WITH_WEEKLY = {
    **ACTIVE_DEFAULTS,
    "weekly_lynkup_request_reminder": True,
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
        "weekly_lynkup_request_reminder": True,
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
    created = _preference(user_id=user_id, category_preferences=dict(MERGED_WITH_WEEKLY))

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
    assert response.data.category_preferences == MERGED_WITH_WEEKLY
    assert response.data.email_preferences == {
        "bulk_email": True,
    }
    defaults.assert_awaited()
    create.assert_awaited_once_with(
        db,
        user_id=user_id,
        category_preferences=MERGED_WITH_WEEKLY,
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
        category_preferences=dict(MERGED_WITH_WEEKLY),
    )
    updated = _preference(
        user_id=user_id,
        push_enabled=False,
        in_app_enabled=True,
        category_preferences={
            **MERGED_WITH_WEEKLY,
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
    existing = _preference(user_id=user_id, category_preferences=dict(MERGED_WITH_WEEKLY))
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
    assert "weekly_lynkup_request_reminder" in saved


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


@pytest.mark.asyncio
async def test_list_notifications_includes_totalcount_above_page():
    from datetime import datetime, timezone
    from apps.notifications.db_models import Notification, NotificationType

    user_id = uuid4()
    type_id = uuid4()
    notif_type = NotificationType(id=type_id, name="CONNECTION_REQUEST", is_active=True)

    n1 = Notification(
        id=uuid4(),
        recipient_user_id=user_id,
        notification_type_id=type_id,
        title="Test 1",
        body="Body 1",
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )
    n1.notification_type = notif_type

    n2 = Notification(
        id=uuid4(),
        recipient_user_id=user_id,
        notification_type_id=type_id,
        title="Test 2",
        body="Body 2",
        is_read=True,
        read_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    n2.notification_type = notif_type

    db = AsyncMock()
    preference = _preference(user_id=user_id)

    with (
        patch.object(svc, "_get_or_create_preferences", AsyncMock(return_value=preference)),
        patch.object(
            svc,
            "_list_unified_notifications_for_user",
            AsyncMock(return_value=[(n1, False, False, None), (n2, False, True, n2.read_at)]),
        ),
        patch.object(
            svc,
            "get_unread_notification_count",
            AsyncMock(return_value=1),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id, page=1, page_size=10)

    data = response.data
    assert data["Totalcount"] == 1
    assert data["page"] == 1
    assert data["pageSize"] == 10
    assert data["totalItems"] == 2
    assert data["reason"] == {}
    assert list(data.keys()) == [
        "items",
        "Totalcount",
        "page",
        "pageSize",
        "totalItems",
        "totalPages",
        "reason",
    ]


@pytest.mark.asyncio
async def test_list_notifications_unpaginated_includes_totalcount():
    from datetime import datetime, timezone
    from apps.notifications.db_models import Notification, NotificationType

    user_id = uuid4()
    type_id = uuid4()
    notif_type = NotificationType(id=type_id, name="CONNECTION_REQUEST", is_active=True)

    n1 = Notification(
        id=uuid4(),
        recipient_user_id=user_id,
        notification_type_id=type_id,
        title="Test 1",
        body="Body 1",
        is_read=False,
        created_at=datetime.now(timezone.utc),
    )
    n1.notification_type = notif_type

    db = AsyncMock()
    preference = _preference(user_id=user_id)

    with (
        patch.object(svc, "_get_or_create_preferences", AsyncMock(return_value=preference)),
        patch.object(
            svc,
            "_list_unified_notifications_for_user",
            AsyncMock(return_value=[(n1, False, False, None)]),
        ),
        patch.object(
            svc,
            "get_unread_notification_count",
            AsyncMock(return_value=1),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id)

    data = response.data
    assert data["Totalcount"] == 1
    assert data["reason"] == {}
    assert list(data.keys()) == ["items", "Totalcount", "reason"]


@pytest.mark.asyncio
async def test_list_notifications_returns_reason_when_in_app_disabled(mock_db):
    db = mock_db()
    user_id = uuid4()
    preference = _preference(user_id=user_id, in_app_enabled=False)

    with (
        patch.object(
            svc,
            "_get_or_create_preferences",
            AsyncMock(return_value=preference),
        ),
        patch.object(
            svc,
            "get_unread_notification_count",
            AsyncMock(return_value=0),
        ),
    ):
        response = await svc.list_notifications(db, user_id=user_id)

    data = response.data
    assert data["items"] == []
    assert data["Totalcount"] == 0
    assert data["reason"] == {
        "code": "IN_APP_NOTIFICATIONS_DISABLED",
        "message": "In-app notifications are turned off. Please turn them on to view your available notifications.",
    }


@pytest.mark.asyncio
async def test_list_notifications_paginated_returns_reason_when_in_app_disabled(mock_db):
    db = mock_db()
    user_id = uuid4()
    preference = _preference(user_id=user_id, in_app_enabled=False)

    with (
        patch.object(
            svc,
            "_get_or_create_preferences",
            AsyncMock(return_value=preference),
        ),
        patch.object(
            svc,
            "get_unread_notification_count",
            AsyncMock(return_value=0),
        ),
    ):
        response = await svc.list_notifications(
            db,
            user_id=user_id,
            page=1,
            page_size=20,
        )

    data = response.data
    assert data["items"] == []
    assert data["Totalcount"] == 0
    assert data["totalItems"] == 0
    assert data["reason"] == {
        "code": "IN_APP_NOTIFICATIONS_DISABLED",
        "message": "In-app notifications are turned off. Please turn them on to view your available notifications.",
    }


@pytest.mark.asyncio
async def test_update_preferences_partial_email_bulk_only(mock_db):
    db = mock_db()
    user_id = uuid4()
    existing = _preference(user_id=user_id)
    updated = _preference(
        user_id=user_id,
        email_preferences={
            "bulk_email": False,
        },
    )
    payload = UpdateNotificationPreferencesRequest(
        email_preferences={"bulk_email": False},
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
    assert response.data is not None
    assert response.data.email_preferences == {
        "bulk_email": False,
    }
    # First persist may be from get-or-create merge; last call is the email update.
    email_calls = [
        c.kwargs.get("email_preferences")
        for c in persist.await_args_list
        if c.kwargs.get("email_preferences") is not None
    ]
    assert email_calls[-1] == {"bulk_email": False}


@pytest.mark.asyncio
async def test_update_preferences_partial_category_weekly_only(mock_db):
    db = mock_db()
    user_id = uuid4()
    existing = _preference(user_id=user_id)
    updated = _preference(
        user_id=user_id,
        category_preferences={
            **MERGED_WITH_WEEKLY,
            "weekly_lynkup_request_reminder": False,
        },
    )
    payload = UpdateNotificationPreferencesRequest(
        category_preferences={"weekly_lynkup_request_reminder": False},
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

    assert response.data.category_preferences["weekly_lynkup_request_reminder"] is False
    assert "weekly_lynkup_request_reminder" not in response.data.email_preferences
    assert response.data.email_preferences["bulk_email"] is True
    category_calls = [
        c.kwargs.get("category_preferences")
        for c in persist.await_args_list
        if c.kwargs.get("category_preferences") is not None
    ]
    assert category_calls[-1]["weekly_lynkup_request_reminder"] is False


def test_update_preferences_rejects_weekly_in_email_preferences():
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc:
        UpdateNotificationPreferencesRequest(
            email_preferences={"weekly_lynkup_request_reminder": False},
        )
    assert "Unsupported email preference keys" in str(exc.value)


def test_update_preferences_rejects_unsupported_email_keys():
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc:
        UpdateNotificationPreferencesRequest(
            email_preferences={"export_email": False},
        )
    assert "Unsupported email preference keys" in str(exc.value)


def test_is_email_preference_enabled_defaults_true_when_missing():
    from apps.notifications.email_preferences import (
        EMAIL_PREF_BULK_EMAIL,
        is_email_preference_enabled,
    )

    assert is_email_preference_enabled(None, EMAIL_PREF_BULK_EMAIL) is True
    assert is_email_preference_enabled({"email_preferences": {}}, EMAIL_PREF_BULK_EMAIL) is True
    pref = SimpleNamespace(email_preferences={})
    assert is_email_preference_enabled(pref, EMAIL_PREF_BULK_EMAIL) is True
    pref_disabled = SimpleNamespace(email_preferences={"bulk_email": False})
    assert is_email_preference_enabled(pref_disabled, EMAIL_PREF_BULK_EMAIL) is False


@pytest.mark.asyncio
async def test_filter_users_eligible_for_email_preference(mock_db, scalar_result):
    from apps.notifications.email_preferences import EMAIL_PREF_BULK_EMAIL
    from apps.notifications.repositories import notification_repository as repo

    enabled = uuid4()
    disabled = uuid4()
    missing = uuid4()
    prefs = [
        SimpleNamespace(
            user_id=enabled,
            email_preferences={"bulk_email": True},
        ),
        SimpleNamespace(
            user_id=disabled,
            email_preferences={"bulk_email": False},
        ),
    ]
    db = mock_db(scalar_result(values=prefs))

    result = await repo.filter_users_eligible_for_email_preference(
        db,
        [enabled, disabled, missing],
        preference=EMAIL_PREF_BULK_EMAIL,
    )
    assert result == [enabled, missing]
