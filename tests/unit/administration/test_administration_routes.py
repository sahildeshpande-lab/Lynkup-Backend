from __future__ import annotations

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.administration import routes as admin_routes
from apps.accounts.schemas import ApiResponse
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_superadmin
from entrypoints.api import app


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

    async def delete(self, *_args, **_kwargs):
        return None


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
    app.dependency_overrides[get_current_superadmin] = _override_admin


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(get_current_superadmin, None)


async def _mock_admin_signup(payload, _db) -> ApiResponse:
    return ApiResponse(
        status=True,
        message="Signup successful",
        data={
            "accessToken": "admin-token",
            "refreshToken": "admin-refresh-token",
            "user": {"email": payload.email, "role": payload.role},
            "emailSent": False,
        },
    )


async def _list_users(_page: int, _page_size: int, _db, search: str | None = None) -> dict:
    return {"items": [], "page": 1, "pageSize": 10, "totalItems": 1, "totalPages": 1}


async def _get_user(_user_id: str, _db) -> dict:
    return {"found": True, "userId": str(_user_id)}


async def _delete_user(_user_id: str, _db) -> dict:
    return {"userId": str(_user_id), "deleted": True}


async def _update_status(_user_id: str, status, _db) -> dict:
    return {"userId": str(_user_id), "status": status.value}


async def _export_users(_page: int | None, _page_size: int | None, _db) -> dict:
    page = _page or 1
    page_size = _page_size or 5
    return {"items": [], "page": page, "pageSize": page_size, "totalItems": 0, "totalPages": 0}


def test_admin_signup_returns_success_payload(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "admin_signup", _mock_admin_signup)

    response = client.post(
        "/api/v1/auth/admin/signup",
        json={
            "firstName": "Ada",
            "lastName": "Lovelace",
            "email": "ada@example.com",
            "password": "Secret123",
            "role": "superadmin",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
    assert body["data"]["user"]["email"] == "ada@example.com"


def test_admin_login_route(monkeypatch) -> None:
    async def _mock_admin_signin(payload, db):
        return ApiResponse(
            status=True,
            message="Login successful",
            data={
                "accessToken": "admin_token_123",
                "refreshToken": "admin_refresh_token_123",
                "user": {"email": payload.email, "role": "superadmin"},
            },
        )

    monkeypatch.setattr(admin_routes.services, "admin_signin", _mock_admin_signin)

    response_login = client.post(
        "/api/v1/auth/admin/login",
        json={"email": "admin@example.com", "password": "Password123"},
    )
    assert response_login.status_code == 200
    assert response_login.json()["status"] is True
    assert response_login.json()["data"]["accessToken"] == "admin_token_123"


def test_admin_onboarding_returns_success_payload(monkeypatch) -> None:
    async def _mock_admin_complete_onboarding(
        user_id,
        bio,
        major,
        minor,
        university_id,
        education_level_id,
        academic_interests,
        profile_photo,
        db,
    ):
        return {"user_id": str(user_id), "onboarded": True}

    monkeypatch.setattr(admin_routes.services, "admin_complete_onboarding", _mock_admin_complete_onboarding)

    import io

    response = client.post(
        "/api/v1/admin/onboarding",
        data={
            "university_id": "11111111-1111-1111-1111-111111111111",
            "major": "Computer Science",
            "minor": "Math",
            "education_level_id": 2,
            "Bio": "Test Bio",
            "academic_interests": "['Math', 'CS']",
        },
        files={"profile_photo": ("test.png", io.BytesIO(b"dummy image data"), "image/png")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
    assert body["data"]["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert body["data"]["onboarded"] is True


def test_admin_list_users_returns_paginated_payload(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "list_users", _list_users)

    response = client.get("/api/v1/users", params={"page": 1, "pageSize": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["page"] == 1
    assert body["data"]["pageSize"] == 10
    assert body["data"]["totalItems"] == 1


def test_admin_user_routes_use_users_path(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "admin_get_user", _get_user)
    monkeypatch.setattr(admin_routes.services, "admin_delete_user", _delete_user)

    user_id = "11111111-1111-1111-1111-111111111111"
    get_response = client.get(f"/api/v1/users/{user_id}")
    delete_response = client.delete(f"/api/v1/users/{user_id}")

    assert get_response.status_code == 200
    assert get_response.json()["data"]["found"] is True
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["deleted"] is True


def test_admin_update_user_status(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "admin_update_user_status", _update_status)

    user_id = "11111111-1111-1111-1111-111111111111"
    suspend_response = client.patch(f"/api/v1/users/{user_id}/status", json={"status": "suspended"})
    ban_response = client.patch(f"/api/v1/users/{user_id}/status", json={"status": "banned"})

    assert suspend_response.status_code == 200
    assert suspend_response.json()["data"]["status"] == "suspended"
    assert ban_response.status_code == 200
    assert ban_response.json()["data"]["status"] == "banned"


def test_admin_export_users(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "export_users", _export_users)

    response_default = client.get("/api/v1/export")
    assert response_default.status_code == 200
    assert response_default.json()["status"] is True
    assert response_default.json()["data"]["page"] == 1
    assert response_default.json()["data"]["pageSize"] == 5

    response_paginated = client.get("/api/v1/export", params={"page": 2, "pageSize": 10})
    assert response_paginated.status_code == 200
    assert response_paginated.json()["status"] is True
    assert response_paginated.json()["data"]["page"] == 2
    assert response_paginated.json()["data"]["pageSize"] == 10


def test_update_completeness_weights(monkeypatch) -> None:
    from apps.profiles import services as profiles_services

    async def _mock_update_completeness_weights(payload, db):
        return {
            "message": "Completeness weights updated and all profiles recalculated.",
            "weights": {
                "bio": 15.0,
                "university": 10.0,
                "major": 10.0,
                "edu_level": 10.0,
                "first_name": 10.0,
                "last_name": 10.0,
                "email": 10.0,
                "profile_photo_url": 10.0,
                "interests": 10.0,
                "graduation_date": 10.0,
                "location": 10.0,
            },
        }

    monkeypatch.setattr(profiles_services, "update_completeness_weights", _mock_update_completeness_weights)

    response = client.patch(
        "/api/v1/update/completeness",
        json={"bio": 15.0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["weights"]["bio"] == 15.0
