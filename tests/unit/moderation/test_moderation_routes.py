from __future__ import annotations

from fastapi.testclient import TestClient

from apps.moderation import routes as moderation_routes
from core.database.session import get_session
from entrypoints.api import app

client = TestClient(app)


class _NoopSession:
    pass


async def _override_session():
    yield _NoopSession()


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)


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
    async def _mock_update(payload, _db):
        assert payload.profanityWords == ["word2"]
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
