from __future__ import annotations

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.threshold_configuration import routes as threshold_routes
from core.database.session import get_session
from core.security.auth import get_current_moderator_or_viewer
from entrypoints.api import app
from apps.threshold_configuration.schemas import ModerationThresholdsData


client = TestClient(app)


class _NoopSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("db session should not be used in this route test")

    def add(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None

    async def refresh(self, *_args, **_kwargs):
        return None


async def _override_session():
    yield _NoopSession()


async def _override_admin():
    u = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        firebase_uid="admin-uid",
    )
    u.role = "superadmin"
    return u


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_admin


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_moderator_or_viewer, None)


def test_get_admin_threshold_route(monkeypatch) -> None:
    async def _mock_get(_db):
        return ModerationThresholdsData(post=10, comment=5, user=10)

    monkeypatch.setattr(threshold_routes, "get_moderation_thresholds", _mock_get)
    response = client.get("/api/v1/admin/threshold")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Moderation thresholds retrieved successfully"
    assert body["data"] == {"post": 10, "comment": 5, "user": 10}


def test_patch_admin_threshold_route(monkeypatch) -> None:
    async def _mock_update(payload, _db, **_kwargs):
        assert payload.post == 15
        assert payload.comment is None
        return ModerationThresholdsData(post=15, comment=5, user=10)

    monkeypatch.setattr(threshold_routes, "update_moderation_thresholds", _mock_update)
    response = client.patch("/api/v1/admin/threshold", json={"post": 15})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Moderation thresholds updated successfully"
    assert body["data"]["post"] == 15


def test_get_admin_threshold_route_viewer(monkeypatch) -> None:
    async def _override_viewer():
        u = User(
            id="22222222-2222-2222-2222-222222222222",
            email="viewer@example.com",
            firebase_uid="viewer-uid",
        )
        u.role = "viewer"
        return u

    async def _mock_get(_db):
        return ModerationThresholdsData(post=10, comment=5, user=10)

    monkeypatch.setattr(threshold_routes, "get_moderation_thresholds", _mock_get)
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_viewer
    try:
        response = client.get("/api/v1/admin/threshold")
        assert response.status_code == 200
        assert response.json()["status"] is True
    finally:
        app.dependency_overrides[get_current_moderator_or_viewer] = _override_admin
