from __future__ import annotations

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.recommendation import routes as recommendation_routes
from apps.recommendation.schemas import SemanticScholarPaper, SemanticScholarSearchData
from apps.recommendation.services import SemanticScholarAPIError
from core.security.auth import get_current_admin
from entrypoints.api import app

client = TestClient(app)


async def _override_admin():
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        firebase_uid="admin-uid",
    )
    user.role = "superadmin"
    return user


def setup_module() -> None:
    app.dependency_overrides[get_current_admin] = _override_admin


def teardown_module() -> None:
    app.dependency_overrides.pop(get_current_admin, None)


def test_ai_scholar_test_route_success(monkeypatch) -> None:
    async def _mock_search(query: str, limit: int = 10):
        assert query == "machine learning"
        assert limit == 5
        return SemanticScholarSearchData(
            total=1,
            offset=0,
            next=1,
            papers=[
                SemanticScholarPaper(
                    paperId="abc123",
                    title="Test Paper",
                    year=2024,
                    citationCount=3,
                )
            ],
        )

    monkeypatch.setattr(recommendation_routes, "search_papers", _mock_search)

    response = client.get(
        "/api/v1/admin/ai-scholar/test",
        params={"query": "machine learning", "limit": 5},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Papers fetched successfully"
    assert body["data"]["total"] == 1
    assert body["data"]["papers"][0]["paperId"] == "abc123"


def test_ai_scholar_test_route_handles_api_error(monkeypatch) -> None:
    async def _mock_search(query: str, limit: int = 10):
        raise SemanticScholarAPIError("Semantic Scholar API request timed out")

    monkeypatch.setattr(recommendation_routes, "search_papers", _mock_search)

    response = client.get(
        "/api/v1/admin/ai-scholar/test",
        params={"query": "covid"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Semantic Scholar API request timed out"
    assert body["data"] is None


def test_ai_scholar_test_route_requires_query() -> None:
    response = client.get(
        "/api/v1/admin/ai-scholar/test",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
