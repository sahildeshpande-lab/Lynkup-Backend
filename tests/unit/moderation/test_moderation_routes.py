from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.administration.dependencies import require_signed_superadmin
from apps.moderation import routes as moderation_routes
from core.database.session import get_session
from entrypoints.api import app

client = TestClient(app)


class _NoopSession:
    pass


async def _override_session():
    yield _NoopSession()


async def _override_superadmin():
    return MagicMock(id=uuid4(), role="superadmin", email="superadmin@example.com")


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[require_signed_superadmin] = _override_superadmin


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(require_signed_superadmin, None)


def test_get_moderation_words_route(monkeypatch) -> None:
    async def _mock_get(_db):
        return {
            "profanityWords": ["word1"],
        }

    monkeypatch.setattr(moderation_routes, "get_moderation_words", _mock_get)

    response = client.get("/api/v1/moderation-words")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Words fetched successfully"
    assert body["data"]["profanityWords"] == ["word1"]
    assert "spamWords" not in body["data"]


def test_update_moderation_words_route(monkeypatch) -> None:
    async def _mock_update(payload, _db, *, actor_user_id=None, actor_role=None):
        assert payload.profanityWords == ["word2"]
        assert actor_user_id is not None
        assert actor_role == "superadmin"
        return {"profanityWords": ["word2"]}

    monkeypatch.setattr(moderation_routes, "update_moderation_words", _mock_update)

    response = client.post(
        "/api/v1/moderation-words",
        json={
            "profanityWords": ["word2"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Words updated successfully"
    assert body["data"] == {"profanityWords": ["word2"]}
