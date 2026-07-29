from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.recommendation import routes as recommendation_routes
from core.database.session import get_session
from core.security.auth import get_current_user
from entrypoints.api import app

client = TestClient(app)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")


async def _override_user():
    user = User(
        id=USER_ID,
        email="user@example.com",
        firebase_uid="user-uid",
    )
    user.role = "user"
    return user


async def _override_session_with_keywords():
    profile = SimpleNamespace(
        user_id=USER_ID,
        extracted_keywords={
            "major": ["Artificial Intelligence"],
            "minor": ["Data Science"],
            "interests": ["Deep Learning"],
            "engagement_keywords": {"rag": 4},
            "content_keywords": {"semantic search": 5},
            "hashtags": {"machinelearning": 3},
        },
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: profile)
    )
    yield db


async def _override_session_without_keywords():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
    yield db


@pytest.fixture(autouse=True)
def _setup_overrides():
    app.dependency_overrides[get_current_user] = _override_user
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)


def test_search_recommendation_papers_returns_raw_semantic_scholar_response(monkeypatch) -> None:
    raw_response = {
        "total": 1,
        "token": "next-token",
        "data": [
            {
                "paperId": "abc123",
                "title": "Test Paper",
                "citationCount": 3,
            }
        ],
    }

    async def _mock_search_papers(query: str, **kwargs):
        assert "artificial intelligence" in query
        assert "semantic search" in query
        return raw_response, 200

    monkeypatch.setattr(recommendation_routes, "search_papers", _mock_search_papers)
    app.dependency_overrides[get_session] = _override_session_with_keywords

    response = client.get(
        "/api/v1/recommendations/papers",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Papers fetched successfully"
    assert body["data"] == raw_response


def test_search_recommendation_papers_returns_empty_message_when_no_papers(monkeypatch) -> None:
    async def _mock_search_papers(query: str, **kwargs):
        return {"data": [], "total": 0}, 200

    monkeypatch.setattr(recommendation_routes, "search_papers", _mock_search_papers)
    app.dependency_overrides[get_session] = _override_session_with_keywords

    response = client.get(
        "/api/v1/recommendations/papers",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "No papers found"
    assert body["data"] == {"data": [], "total": 0}


def test_search_recommendation_papers_returns_rate_limit_message(monkeypatch) -> None:
    async def _mock_search_papers(query: str, **kwargs):
        return {"data": [], "total": 0}, 429

    monkeypatch.setattr(recommendation_routes, "search_papers", _mock_search_papers)
    app.dependency_overrides[get_session] = _override_session_with_keywords

    response = client.get(
        "/api/v1/recommendations/papers",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert "rate limit" in body["message"].lower()
    assert body["data"] == {"data": [], "total": 0}


def test_search_recommendation_papers_without_profile_keywords() -> None:
    app.dependency_overrides[get_session] = _override_session_without_keywords

    response = client.get(
        "/api/v1/recommendations/papers",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "No profile keywords available for recommendations"
    assert body["data"] == {"data": [], "total": 0}


def test_search_recommendation_papers_requires_authentication() -> None:
    app.dependency_overrides.pop(get_current_user, None)

    response = client.get("/api/v1/recommendations/papers")

    assert response.status_code == 401
