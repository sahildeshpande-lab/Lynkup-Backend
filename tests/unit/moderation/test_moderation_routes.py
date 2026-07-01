from __future__ import annotations

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.moderation import routes as moderation_routes
from core.database.session import get_session
from core.security.auth import get_current_admin
from entrypoints.api import app

client = TestClient(app)


class _NoopSession:
    pass


async def _override_session():
    yield _NoopSession()


async def _override_admin():
    return User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        role="superadmin",
        firebase_uid="admin-uid",
    )


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_admin] = _override_admin


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_admin, None)


def test_get_moderation_words_route(monkeypatch) -> None:
    async def _mock_get(_db):
        return {
            "profanityWords": ["word1"],
        }

    monkeypatch.setattr(moderation_routes, "get_moderation_words", _mock_get)

    response = client.get(
        "/api/v1/admin/moderation-words",
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Words fetched successfully"
    assert body["data"]["profanityWords"] == ["word1"]
    assert "spamWords" not in body["data"]


def test_update_moderation_words_route(monkeypatch) -> None:
    async def _mock_update(payload, _db):
        assert payload.profanityWords == ["word2"]
        return {"profanityWords": ["word2"]}

    monkeypatch.setattr(moderation_routes, "update_moderation_words", _mock_update)

    response = client.post(
        "/api/v1/admin/moderation-words",
        json={
            "profanityWords": ["word2"],
        },
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Words updated successfully"
    assert body["data"] == {"profanityWords": ["word2"]}
