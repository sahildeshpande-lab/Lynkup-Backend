from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.administration import routes as admin_routes
from core.database.session import get_session
from core.security.auth import (
    get_current_admin,
    get_current_moderator,
    get_current_moderator_or_viewer,
    get_current_superadmin,
    get_current_user,
    get_current_user_or_superadmin,
)
from apps.administration.dependencies import (
    require_admin_signed_request,
    require_signed_admin,
    require_signed_moderator,
    require_signed_moderator_or_viewer,
)
from entrypoints.api import app

client = TestClient(app)

_MODERATOR_ID = uuid4()
_SUPERADMIN_ID = uuid4()
_VIEWER_ID = uuid4()
_USER_ID = uuid4()


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


async def _override_superadmin():
    u = User(id=_SUPERADMIN_ID, email="superadmin@example.com", firebase_uid="sa-uid")
    u.role = "superadmin"
    return u


async def _override_moderator():
    u = User(id=_MODERATOR_ID, email="moderator@example.com", firebase_uid="mod-uid")
    u.role = "moderator"
    return u


async def _override_viewer():
    u = User(id=_VIEWER_ID, email="viewer@example.com", firebase_uid="viewer-uid")
    u.role = "viewer"
    return u


async def _override_app_user():
    u = User(id=_USER_ID, email="user@example.com", firebase_uid="user-uid")
    u.role = "user"
    return u


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_moderator] = _override_moderator
    app.dependency_overrides[require_signed_moderator] = _override_moderator
    app.dependency_overrides[get_current_superadmin] = _override_superadmin
    app.dependency_overrides[get_current_admin] = _override_superadmin
    app.dependency_overrides[require_signed_admin] = _override_superadmin
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator
    app.dependency_overrides[require_signed_moderator_or_viewer] = _override_moderator
    app.dependency_overrides[get_current_user_or_superadmin] = _override_app_user


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_moderator, None)
    app.dependency_overrides.pop(require_signed_moderator, None)
    app.dependency_overrides.pop(get_current_superadmin, None)
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(require_signed_admin, None)
    app.dependency_overrides.pop(get_current_moderator_or_viewer, None)
    app.dependency_overrides.pop(require_signed_moderator_or_viewer, None)
    app.dependency_overrides.pop(get_current_user_or_superadmin, None)
    app.dependency_overrides.pop(get_current_user, None)


def _log_item(**overrides) -> dict:
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
        "metadata": {"old": {"status": "processing"}, "new": {"status": "rejected"}},
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return data


def test_moderator_can_access_activity_logs(monkeypatch) -> None:
    async def _mock_list(_db, **kwargs):
        assert kwargs.get("moderator_id") is None
        assert kwargs["role"] is None
        assert kwargs["search"] is None
        return {"items": [_log_item()]}

    monkeypatch.setattr(admin_routes.services, "list_admin_activity_logs_service", _mock_list)
    response = client.get("/api/v1/admin/activity-logs")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Activity logs retrieved successfully"
    assert len(body["data"]["items"]) == 1
    assert "page" not in body["data"]


def test_superadmin_can_access_activity_logs(monkeypatch) -> None:
    app.dependency_overrides[get_current_moderator] = _override_superadmin
    app.dependency_overrides[require_signed_moderator] = _override_superadmin

    async def _mock_list(_db, **kwargs):
        return {"items": []}

    monkeypatch.setattr(admin_routes.services, "list_admin_activity_logs_service", _mock_list)
    try:
        response = client.get("/api/v1/admin/activity-logs")
        assert response.status_code == 200
        assert response.json()["status"] is True
        assert response.json()["data"]["items"] == []
    finally:
        app.dependency_overrides[get_current_moderator] = _override_moderator
        app.dependency_overrides[require_signed_moderator] = _override_moderator


def test_viewer_cannot_access_activity_logs() -> None:
    # Real require_signed_moderator role check must run on an authenticated principal.
    app.dependency_overrides.pop(require_signed_moderator, None)
    app.dependency_overrides[require_admin_signed_request] = _override_viewer
    try:
        response = client.get("/api/v1/admin/activity-logs")
        assert response.status_code == 403
        assert response.json()["status"] is False
        assert "permission" in response.json()["message"].lower()
    finally:
        app.dependency_overrides.pop(require_admin_signed_request, None)
        app.dependency_overrides[get_current_moderator] = _override_moderator
        app.dependency_overrides[require_signed_moderator] = _override_moderator


def test_user_cannot_access_activity_logs() -> None:
    app.dependency_overrides.pop(require_signed_moderator, None)
    app.dependency_overrides[require_admin_signed_request] = _override_app_user
    try:
        response = client.get("/api/v1/admin/activity-logs")
        assert response.status_code == 403
        assert response.json()["status"] is False
    finally:
        app.dependency_overrides.pop(require_admin_signed_request, None)
        app.dependency_overrides[get_current_moderator] = _override_moderator
        app.dependency_overrides[require_signed_moderator] = _override_moderator


def test_activity_logs_use_common_pagination(monkeypatch) -> None:
    captured = {}

    async def _mock_list(_db, **kwargs):
        captured.update(kwargs)
        return {
            "items": [_log_item()],
            "page": 2,
            "pageSize": 10,
            "totalItems": 21,
            "totalPages": 3,
        }

    monkeypatch.setattr(admin_routes.services, "list_admin_activity_logs_service", _mock_list)
    response = client.get(
        "/api/v1/admin/activity-logs",
        params={"page": 2, "pageSize": 10},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["page"] == 2
    assert body["data"]["pageSize"] == 10
    assert body["data"]["totalItems"] == 21
    assert captured["page"] == 2
    assert captured["page_size"] == 10


def test_activity_logs_search(monkeypatch) -> None:
    captured = {}

    async def _mock_list(_db, **kwargs):
        captured.update(kwargs)
        return {"items": [_log_item(action="rejected", module="post")]}

    monkeypatch.setattr(admin_routes.services, "list_admin_activity_logs_service", _mock_list)
    response = client.get("/api/v1/admin/activity-logs", params={"search": "rejected"})
    assert response.status_code == 200
    assert captured["search"] == "rejected"


def test_activity_logs_applies_moderator_id_filter(monkeypatch) -> None:
    captured = {}

    async def _mock_list(_db, **kwargs):
        captured.update(kwargs)
        return {"items": []}

    monkeypatch.setattr(admin_routes.services, "list_admin_activity_logs_service", _mock_list)
    response = client.get(
        "/api/v1/admin/activity-logs",
        params={
            "moderator_id": str(_MODERATOR_ID),
            "role": "superadmin",
            "module": "user",
            "search": "feature",
        },
    )
    assert response.status_code == 200
    assert captured["moderator_id"] == _MODERATOR_ID
    assert captured["role"].value == "superadmin"
    assert captured["module"] == "user"
    assert captured["search"] == "feature"


def test_activity_logs_role_and_module_filters(monkeypatch) -> None:
    from common.enums import AdminActivityLogRole

    captured = {}

    async def _mock_list(_db, **kwargs):
        captured.update(kwargs)
        return {"items": [_log_item(role="moderator", module="user")]}

    monkeypatch.setattr(admin_routes.services, "list_admin_activity_logs_service", _mock_list)
    response = client.get(
        "/api/v1/admin/activity-logs",
        params={"role": "moderator", "module": "user"},
    )
    assert response.status_code == 200
    assert captured["role"] == AdminActivityLogRole.moderator
    assert captured["module"] == "user"


def test_activity_logs_rejects_invalid_role() -> None:
    response = client.get(
        "/api/v1/admin/activity-logs",
        params={"role": "viewer"},
    )
    assert response.status_code == 200
    assert response.json()["status"] is False


def test_activity_logs_module_filter_and_sort(monkeypatch) -> None:
    from common.enums import AdminActivityLogOrder, AdminActivityLogSort

    captured = {}

    async def _mock_list(_db, **kwargs):
        captured.update(kwargs)
        return {"items": [_log_item(module="post")]}

    monkeypatch.setattr(admin_routes.services, "list_admin_activity_logs_service", _mock_list)
    response = client.get(
        "/api/v1/admin/activity-logs",
        params={"module": "post", "sort": "module", "order": "asc"},
    )
    assert response.status_code == 200
    assert captured["module"] == "post"
    assert captured["sort"] == AdminActivityLogSort.module
    assert captured["order"] == AdminActivityLogOrder.asc


def test_activity_logs_sort_defaults_to_created_at_desc(monkeypatch) -> None:
    from common.enums import AdminActivityLogOrder, AdminActivityLogSort

    captured = {}

    async def _mock_list(_db, **kwargs):
        captured.update(kwargs)
        return {"items": []}

    monkeypatch.setattr(admin_routes.services, "list_admin_activity_logs_service", _mock_list)
    response = client.get("/api/v1/admin/activity-logs")
    assert response.status_code == 200
    assert captured["sort"] == AdminActivityLogSort.created_at
    assert captured["order"] == AdminActivityLogOrder.desc


def test_activity_logs_rejects_invalid_sort() -> None:
    response = client.get(
        "/api/v1/admin/activity-logs",
        params={"sort": "not_a_column", "order": "desc"},
    )
    assert response.status_code == 200
    assert response.json()["status"] is False
