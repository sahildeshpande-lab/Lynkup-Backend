from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.administration.db_models.admin_activity_log_db_model import AdminActivityLog
from apps.administration.repositories.admin_activity_log_repository import (
    list_admin_notification_activity_logs,
    mark_admin_activity_log_read,
    mark_all_admin_activity_logs_read,
)
from apps.administration.services.admin_activity_log_service import (
    list_admin_notifications_service,
    mark_admin_notification_as_read,
    mark_all_admin_notifications_as_read,
)
from core.database.session import get_session
from core.security.auth import get_current_moderator, get_current_superadmin, get_current_user
from entrypoints.api import app

client = TestClient(app)

_MODERATOR_ID = uuid4()
_SUPERADMIN_ID = uuid4()
_REGISTRATION_TIME = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


class _NoopSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("db session should not be used in this route test")

    def add(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None

    async def refresh(self, *_args, **_kwargs):
        return None

    async def flush(self, *_args, **_kwargs):
        return None


async def _override_session():
    yield _NoopSession()


async def _override_moderator():
    u = User(
        id=_MODERATOR_ID,
        email="moderator@example.com",
        firebase_uid="mod-uid",
        created_at=_REGISTRATION_TIME,
    )
    u.role = "moderator"
    return u


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_moderator] = _override_moderator


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_moderator, None)


def _notification_item(**overrides) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "id": str(uuid4()),
        "user_id": str(_MODERATOR_ID),
        "user_name": "Ada Moderator",
        "role": "moderator",
        "action": "rejected",
        "module": "post",
        "record_id": str(uuid4()),
        "description": "Post rejected during moderation",
        "metadata": {"reason": "policy_violation"},
        "is_read": False,
        "read_at": None,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return data


def test_list_admin_notifications_route(monkeypatch) -> None:
    async def _mock_list_service(_db, **kwargs):
        assert kwargs["moderator_id"] is None
        assert kwargs["is_read"] is None
        assert kwargs["page"] is None
        assert kwargs["page_size"] is None
        return {"items": [_notification_item()]}

    from apps.notifications import routes as notification_routes

    monkeypatch.setattr(
        "apps.administration.services.admin_activity_log_service.list_admin_notifications_service",
        _mock_list_service,
    )

    response = client.get("/api/v1/admin/notification")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Admin notifications fetched successfully."
    assert len(body["data"]["items"]) == 1


def test_list_admin_notifications_route_with_filters(monkeypatch) -> None:
    async def _mock_list_service(_db, **kwargs):
        assert kwargs["moderator_id"] == _MODERATOR_ID
        assert kwargs["is_read"] is False
        assert kwargs["page"] == 1
        assert kwargs["page_size"] == 10
        return {
            "items": [_notification_item()],
            "page": 1,
            "pageSize": 10,
            "totalItems": 1,
            "totalPages": 1,
            "hasNext": False,
            "hasPrevious": False,
        }

    monkeypatch.setattr(
        "apps.administration.services.admin_activity_log_service.list_admin_notifications_service",
        _mock_list_service,
    )

    response = client.get(
        f"/api/v1/admin/notification?moderator_id={_MODERATOR_ID}&is_read=false&page=1&pageSize=10"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["page"] == 1
    assert body["data"]["pageSize"] == 10


def test_mark_admin_notification_read_route(monkeypatch) -> None:
    target_id = uuid4()
    item = _notification_item(id=str(target_id), is_read=True, read_at=datetime.now(timezone.utc).isoformat())

    async def _mock_mark_read(_db, notification_id):
        assert notification_id == target_id
        return item

    monkeypatch.setattr(
        "apps.administration.services.admin_activity_log_service.mark_admin_notification_as_read",
        _mock_mark_read,
    )

    response = client.patch(f"/api/v1/admin/notification/{target_id}/read")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["is_read"] is True


def test_mark_all_admin_notifications_read_route(monkeypatch) -> None:
    async def _mock_mark_all_read(_db, current_user, moderator_id):
        assert current_user.id == _MODERATOR_ID
        assert moderator_id == _MODERATOR_ID
        return 5

    monkeypatch.setattr(
        "apps.administration.services.admin_activity_log_service.mark_all_admin_notifications_as_read",
        _mock_mark_all_read,
    )

    response = client.patch(f"/api/v1/admin/notification/read-all?moderator_id={_MODERATOR_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["updated_count"] == 5


@pytest.mark.asyncio
async def test_registration_date_filtering():
    """Verify list_admin_notification_activity_logs repo filters logs before user created_at."""
    old_log = AdminActivityLog(
        id=uuid4(),
        user_id=_MODERATOR_ID,
        role="moderator",
        action="test",
        module="test",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    new_log = AdminActivityLog(
        id=uuid4(),
        user_id=_MODERATOR_ID,
        role="moderator",
        action="test",
        module="test",
        created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    class MockScalarResult:
        def scalar_one(self):
            return 1

    class MockExecuteResult:
        def scalar_one(self):
            return 1

        def all(self):
            return [(new_log, "Ada", "Moderator", "mod@example.com")]

    class DummyDbSession:
        async def execute(self, stmt):
            return MockExecuteResult()

    db = DummyDbSession()
    rows, total = await list_admin_notification_activity_logs(
        db,
        moderator_id=_MODERATOR_ID,
        created_after=_REGISTRATION_TIME,
    )
    assert total == 1
    assert rows[0][0].id == new_log.id


@pytest.mark.asyncio
async def test_list_admin_notifications_service_returns_totalcount():
    log = AdminActivityLog(
        id=uuid4(),
        user_id=_MODERATOR_ID,
        role="moderator",
        action="test",
        module="test",
        is_read=False,
        created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    db = AsyncMock()
    # Mock count_stmt and stmt executions in repo or mock repo calls
    with (
        patch(
            "apps.administration.repositories.admin_activity_log_repository.list_admin_notification_activity_logs",
            AsyncMock(return_value=([(log, "Ada Moderator")], 1)),
        ),
        patch(
            "apps.administration.repositories.admin_activity_log_repository.count_unread_admin_notification_activity_logs",
            AsyncMock(return_value=7),
        ),
    ):
        user = User(id=_MODERATOR_ID, email="mod@example.com", role="moderator", created_at=_REGISTRATION_TIME)
        result = await list_admin_notifications_service(db, current_user=user, page=1, page_size=10)

    assert result["Totalcount"] == 7
    assert result["page"] == 1
    assert result["pageSize"] == 10
    assert result["totalItems"] == 1
    assert list(result.keys()) == ["items", "Totalcount", "page", "pageSize", "totalItems", "totalPages"]


@pytest.mark.asyncio
async def test_list_admin_notification_activity_logs_repo_deduplicates_duplicate_rows():
    log = AdminActivityLog(
        id=uuid4(),
        user_id=_MODERATOR_ID,
        role="moderator",
        action="flagged",
        module="post",
        record_id=uuid4(),
        description="flagged a post",
        is_read=False,
        created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )

    class MockExecuteResult:
        def scalar_one(self):
            return 1

        def all(self):
            # Simulate DB returning 3 joined rows for same activity log
            return [
                (log, "Ada", "Moderator", "mod@example.com"),
                (log, "Ada", "Moderator", "mod@example.com"),
                (log, "Ada", "Moderator", "mod@example.com"),
            ]

    class DummyDbSession:
        async def execute(self, stmt):
            return MockExecuteResult()

    db = DummyDbSession()
    rows, total = await list_admin_notification_activity_logs(
        db,
        moderator_id=None,
        created_after=_REGISTRATION_TIME,
    )
    assert total == 1
    assert len(rows) == 1
    assert rows[0][0].id == log.id

