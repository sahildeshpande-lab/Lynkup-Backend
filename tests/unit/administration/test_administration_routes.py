from __future__ import annotations

from fastapi.testclient import TestClient

from apps.administration import routes as admin_routes
<<<<<<< HEAD
from core.security.auth import get_session, get_current_user, get_current_superadmin, get_current_admin
=======
from core.security.auth import get_current_superadmin, get_session
>>>>>>> 5038703 (Test cases)
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


<<<<<<< HEAD
from fastapi import Request

=======
>>>>>>> 5038703 (Test cases)
async def _override_session():
    yield _NoopSession()


async def _override_current_user():
    from apps.accounts.db_models import User
<<<<<<< HEAD
    from uuid import UUID
    return User(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        email="admin@example.com",
        role="superadmin",
        firebase_uid="admin-test-uid",
    )


async def _mock_firebase_user(request: Request) -> dict:
    try:
        body = await request.json()
        email = body.get("email", "admin@example.com")
        return {"uid": "admin-test-uid", "email": email}
    except Exception:
        return {"uid": "admin-test-uid", "email": "admin@example.com"}


def setup_module() -> None:
    from core.auth.firebase import get_current_firebase_user, get_firebase_user_from_payload
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_superadmin] = _override_current_user
    app.dependency_overrides[get_current_admin] = _override_current_user
    app.dependency_overrides[get_current_firebase_user] = _mock_firebase_user
    app.dependency_overrides[get_firebase_user_from_payload] = _mock_firebase_user


def teardown_module() -> None:
    from core.auth.firebase import get_current_firebase_user, get_firebase_user_from_payload
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_superadmin, None)
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(get_current_firebase_user, None)
    app.dependency_overrides.pop(get_firebase_user_from_payload, None)
=======

    return User(email="admin@example.com", role="superadmin", firebase_uid="admin-test-uid")


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_superadmin] = _override_current_user


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_superadmin, None)


>>>>>>> 5038703 (Test cases)
async def _list_users(_page: int, _page_size: int, _db) -> dict:
    return {"items": [], "page": 1, "pageSize": 10, "totalItems": 1, "totalPages": 1}


<<<<<<< HEAD
async def _create_user(_payload, _db) -> dict:
    return {"user": {"email": "ada@example.com"}, "created": True}


=======
>>>>>>> 5038703 (Test cases)
async def _get_user(_user_id: str, _db) -> dict:
    return {"user": {"id": _user_id}, "found": True}


<<<<<<< HEAD
=======
async def _update_user(_user_id: str, _payload, _db) -> dict:
    return {"user": {"id": _user_id}, "updated": {"firstName": "Ada"}, "found": True}


>>>>>>> 5038703 (Test cases)
async def _delete_user(_user_id: str, _db) -> dict:
    return {"userId": _user_id, "deleted": True}


<<<<<<< HEAD
async def _export_users(_page: int | None, _page_size: int | None, _db) -> dict:
    page = _page or 1
    page_size = _page_size or 5
    return {"items": [], "page": page, "pageSize": page_size, "totalItems": 0, "totalPages": 0}



=======
>>>>>>> 5038703 (Test cases)
async def _suspend_user(_payload, _db) -> dict:
    return {"id": "11111111-1111-1111-1111-111111111111", "status": "suspended"}


async def _ban_user(_payload, _db) -> dict:
    return {"id": "11111111-1111-1111-1111-111111111111", "status": "banned"}


async def _mock_admin_signup(payload, db) -> dict:
    return {
        "status": True,
        "message": "success",
        "data": {
            "user": {"email": payload.email, "role": payload.role},
<<<<<<< HEAD
            "accessToken": "mock-access-token",
            "refreshToken": "mock-refresh-token",
        }
=======
            "tokens": {
                "access_token": "mock-access-token",
                "refresh_token": "mock-refresh-token",
                "token_type": "bearer",
            },
        },
>>>>>>> 5038703 (Test cases)
    }


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


<<<<<<< HEAD
def test_admin_login_route(monkeypatch) -> None:
    async def _mock_admin_signin(payload, db):
        from apps.accounts.schemas import ApiResponse
        return ApiResponse(
            status=True,
            message="Login successful",
            data={
                "accessToken": "admin_token_123",
                "refreshToken": "admin_refresh_token_123",
                "user": {"email": payload.email, "role": "superadmin"}
            }
        )

    monkeypatch.setattr(admin_routes.services, "admin_signin", _mock_admin_signin)

    # Test /auth/admin/login
    response_login = client.post(
        "/api/v1/auth/admin/login",
        json={"email": "admin@example.com", "password": "Password123"},
    )
    assert response_login.status_code == 200
    assert response_login.json()["status"] is True
    assert response_login.json()["data"]["accessToken"] == "admin_token_123"


def test_admin_onboarding_returns_success_payload(monkeypatch) -> None:
    async def _mock_admin_complete_onboarding(user_id, bio, major, minor, university_id, education_level_id, academic_interests, profile_photo, db):
        return {"user_id": str(user_id), "onboarded": True}

    monkeypatch.setattr(admin_routes.services, "admin_complete_onboarding", _mock_admin_complete_onboarding)

    user_id = "11111111-1111-1111-1111-111111111111"
    import io
    dummy_file = io.BytesIO(b"dummy image data")
    response = client.post(
        "/api/v1/admin/onboarding",
        data={
            "university_id": "11111111-1111-1111-1111-111111111111",
            "major": "Computer Science",
            "minor": "Math",
            "education_level_id": 2,
            "Bio": "Test Bio",
            "academic_interests": "['Math', 'CS']"
        },
        files={
            "profile_photo": ("test.png", dummy_file, "image/png")
        }
=======
def test_admin_education_returns_success_payload(monkeypatch) -> None:
    async def _mock_admin_education(payload, db):
        return {"education": payload.model_dump(), "saved": True}

    monkeypatch.setattr(admin_routes.services, "admin_education", _mock_admin_education)

    response = client.post(
        "/api/v1/auth/admin/education",
        json={
            "universityId": "univ-123",
            "major": "Computer Science",
            "minor": "Math",
            "educationLevel": "Masters",
        },
>>>>>>> 5038703 (Test cases)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
<<<<<<< HEAD
    assert body["data"]["user_id"] == user_id
    assert body["data"]["onboarded"] is True
=======
    assert body["data"]["education"]["universityId"] == "univ-123"
>>>>>>> 5038703 (Test cases)


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
<<<<<<< HEAD
=======
    monkeypatch.setattr(admin_routes.services, "admin_update_user", _update_user)
>>>>>>> 5038703 (Test cases)
    monkeypatch.setattr(admin_routes.services, "admin_delete_user", _delete_user)

    user_id = "11111111-1111-1111-1111-111111111111"
    get_response = client.get(f"/api/v1/users/{user_id}")
<<<<<<< HEAD
=======
    patch_response = client.patch(f"/api/v1/users/{user_id}", json={"firstName": "Ada"})
>>>>>>> 5038703 (Test cases)
    delete_response = client.delete(f"/api/v1/users/{user_id}")

    assert get_response.status_code == 200
    assert get_response.json()["data"]["found"] is True
<<<<<<< HEAD
=======
    assert patch_response.status_code == 200
    assert patch_response.json()["data"]["found"] is True
>>>>>>> 5038703 (Test cases)
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["deleted"] is True


<<<<<<< HEAD
def test_admin_export_users(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "export_users", _export_users)

    # 1. Test without pagination parameters (default case)
    response_default = client.get("/api/v1/export")
    assert response_default.status_code == 200
    assert response_default.json()["status"] is True
    assert response_default.json()["data"]["page"] == 1
    assert response_default.json()["data"]["pageSize"] == 5

    # 2. Test with pagination parameters
    response_paginated = client.get("/api/v1/export", params={"page": 2, "pageSize": 10})
    assert response_paginated.status_code == 200
    assert response_paginated.json()["status"] is True
    assert response_paginated.json()["data"]["page"] == 2
    assert response_paginated.json()["data"]["pageSize"] == 10


=======
>>>>>>> 5038703 (Test cases)
def test_admin_suspend_and_ban_routes_exist(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "admin_suspend_user", _suspend_user)
    monkeypatch.setattr(admin_routes.services, "admin_ban_user", _ban_user)

    user_id = "11111111-1111-1111-1111-111111111111"
<<<<<<< HEAD
    suspend_response = client.post(
        f"/api/v1/users/{user_id}/suspend",
        json={"id": user_id},
    )
    ban_response = client.post(
        f"/api/v1/users/{user_id}/ban",
        json={"id": user_id},
    )
=======
    suspend_response = client.post(f"/api/v1/users/{user_id}/suspend", json={"id": user_id})
    ban_response = client.post(f"/api/v1/users/{user_id}/ban", json={"id": user_id})
>>>>>>> 5038703 (Test cases)

    assert suspend_response.status_code == 200
    assert suspend_response.json()["data"]["status"] == "suspended"
    assert ban_response.status_code == 200
    assert ban_response.json()["data"]["status"] == "banned"
<<<<<<< HEAD


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
                "location": 10.0
            }
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
=======
>>>>>>> 5038703 (Test cases)
