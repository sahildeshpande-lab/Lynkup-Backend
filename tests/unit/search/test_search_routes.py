from fastapi.testclient import TestClient

from apps.search import routes as search_routes
from entrypoints.api import app
from core.security.auth import get_current_user
from apps.accounts.db_models import User

client = TestClient(app)


async def _override_current_user():
    return User(
        email="jane@example.com",
        role="user",
        firebase_uid="test-uid",
    )


def setup_module() -> None:
    app.dependency_overrides[get_current_user] = _override_current_user


def teardown_module() -> None:
    app.dependency_overrides.pop(get_current_user, None)



async def _search_universities(_params, _db) -> dict:
    return {
        "query": "kampu",
        "items": [
            {
                 "id": "uni-1",
                 "name": "Kampu University",
                 "country": "United States",
                 "slug": "kampu-university",
                 "major": [{"name": "Computer Science"}],
                 "minor": [{"name": "Psychology"}],
                 "academic_program": [{"name": "Undergraduate"}],
            }
        ],
        "page": 1,
        "pageSize": 20,
        "totalItems": 1,
        "totalPages": 1,
    }


def test_university_search_rejects_short_query() -> None:
    response = client.get("/api/v1/universities", params={"query": "ab", "page": 1, "pageSize": 20})

    assert response.status_code == 400


def test_university_search_returns_matches(monkeypatch) -> None:
    monkeypatch.setattr(search_routes.services, "search_universities", _search_universities)

    response = client.get("/api/v1/universities", params={"query": "kampu", "page": 1, "pageSize": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Universities fetched successfully"
    assert body["data"]["page"] == 1
    assert body["data"]["pageSize"] == 20
    assert body["data"]["totalItems"] == 1
    assert body["data"]["items"][0]["name"] == "Kampu University"
    assert body["data"]["items"][0]["country"] == "United States"
    assert body["data"]["items"][0]["minor"] == [{"name": "Psychology"}]
