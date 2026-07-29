from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.recommendation import routes as recommendation_routes
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


@pytest.fixture(autouse=True)
def _setup_overrides():
    app.dependency_overrides[get_current_user] = _override_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_search_profile_papers_returns_raw_semantic_scholar_response(monkeypatch) -> None:
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
        assert query == "machine learning fraud detection"
        return raw_response

    monkeypatch.setattr(recommendation_routes, "search_papers", _mock_search_papers)

    response = client.get(
        "/api/v1/recommendations/papers",
        params={"query": "machine learning fraud detection"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Papers fetched successfully"
    assert body["data"] == raw_response


def test_search_profile_papers_returns_empty_message_when_no_papers(monkeypatch) -> None:
    async def _mock_search_papers(query: str, **kwargs):
        return {"data": [], "total": 0}

    monkeypatch.setattr(recommendation_routes, "search_papers", _mock_search_papers)

    response = client.get(
        "/api/v1/recommendations/papers",
        params={"query": "covid"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "No papers found"
    assert body["data"] == {"data": [], "total": 0}


def test_search_profile_papers_requires_query_parameter() -> None:
    response = client.get(
        "/api/v1/recommendations/papers",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False


def test_search_profile_papers_requires_authentication() -> None:
    app.dependency_overrides.pop(get_current_user, None)

    response = client.get(
        "/api/v1/recommendations/papers",
        params={"query": "machine learning"},
    )

    assert response.status_code == 401
