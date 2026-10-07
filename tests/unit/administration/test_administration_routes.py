from __future__ import annotations

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.administration import routes as admin_routes
from apps.accounts.schemas import ApiResponse
from core.database.session import get_session
from core.security.auth import (
    get_current_admin,
    get_current_user_or_superadmin,
    get_current_moderator,
    get_current_superadmin,
    get_current_moderator_or_viewer,
)
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
    u = User(
        id="11111111-1111-1111-1111-111111111111",
        email="admin@example.com",
        firebase_uid="admin-uid",
    )
    u.role = "superadmin"
    return u


async def _override_moderator():
    u = User(
        id="22222222-2222-2222-2222-222222222222",
        email="moderator@example.com",
        firebase_uid="moderator-uid",
    )
    u.role = "moderator"
    return u


async def _override_viewer():
    u = User(
        id="44444444-4444-4444-4444-444444444444",
        email="viewer@example.com",
        firebase_uid="viewer-uid",
    )
    u.role = "viewer"
    return u


async def _override_app_user():
    u = User(
        id="55555555-5555-5555-5555-555555555555",
        email="appuser@example.com",
        firebase_uid="app-user-uid",
    )
    u.role = "user"
    return u


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[get_current_moderator] = _override_moderator
    app.dependency_overrides[get_current_superadmin] = _override_admin
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator
    app.dependency_overrides[get_current_user_or_superadmin] = _override_app_user


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(get_current_moderator, None)
    app.dependency_overrides.pop(get_current_superadmin, None)
    app.dependency_overrides.pop(get_current_moderator_or_viewer, None)
    app.dependency_overrides.pop(get_current_user_or_superadmin, None)


async def _list_users(
    _page: int,
    _page_size: int,
    _db,
    search: str | None = None,
    status=None,
    is_alumni: bool | None = None,
) -> dict:
    _ = status, is_alumni
    return {"items": [], "page": 1, "pageSize": 10, "totalItems": 1, "totalPages": 1}


async def _get_user(_user_id: str, _db) -> dict:
    return {"found": True, "userId": str(_user_id)}


async def _delete_users(_user_ids: list, _role: str, _db, **_kwargs) -> dict:
    return {
        "deleted_users": [
            {
                "deleted": True,
                "status": "deleting",
                "user": {"id": str(uid), "userId": str(uid)},
            }
            for uid in _user_ids
        ]
    }


async def _update_status(
    _user_id: str,
    status,
    _db,
    *,
    moderator_id=None,
    comment=None,
    actor_role=None,
) -> dict:
    from common.enums import format_user_status

    _ = (moderator_id, comment, actor_role)
    return {"userId": str(_user_id), "status": format_user_status(status)}


async def _export_users(_page: int | None, _page_size: int | None, _db) -> dict:
    page = _page or 1
    page_size = _page_size or 5
    return {"items": [], "page": page, "pageSize": page_size, "totalItems": 0, "totalPages": 0}


def test_admin_login_route(monkeypatch) -> None:
    async def _mock_admin_signin(payload, db, request=None):
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


def test_admin_login_route_returns_401_for_non_admin(monkeypatch) -> None:
    from fastapi import HTTPException, status

    async def _mock_admin_signin(payload, db, request=None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Forbidden: Admin access required",
        )

    monkeypatch.setattr(admin_routes.services, "admin_signin", _mock_admin_signin)

    response = client.post(
        "/api/v1/auth/admin/login",
        json={"email": "user@example.com", "password": "Password123"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Forbidden: Admin access required"
    assert body["data"] is None


def test_admin_logout_route(monkeypatch) -> None:
    async def _mock_admin_logout(current_user, db, request=None):
        return ApiResponse(
            status=True,
            message="Logged out successfully",
            data={"sessionId": "11111111-1111-1111-1111-111111111111"},
        )

    monkeypatch.setattr(admin_routes.services, "admin_logout", _mock_admin_logout)

    response = client.post("/api/v1/auth/admin/logout")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Logged out successfully"
    assert body["data"]["sessionId"] == "11111111-1111-1111-1111-111111111111"


def test_admin_signup_route_returns_401_for_invalid_role(monkeypatch) -> None:
    from fastapi import HTTPException, status

    async def _mock_admin_signup(payload, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Forbidden: Admin access required",
        )

    monkeypatch.setattr(admin_routes.services, "admin_signup", _mock_admin_signup)

    response = client.post(
        "/api/v1/auth/admin/signup",
        json={
            "firstName": "Admin",
            "lastName": "User",
            "email": "user@example.com",
            "password": "Secret123",
            "role": "user",
        },
    )
    assert response.status_code == 401
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Forbidden: Admin access required"
    assert body["data"] is None


def test_admin_list_users_returns_paginated_payload(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "list_users", _list_users)

    response = client.get("/api/v1/users", params={"page": 1, "pageSize": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["page"] == 1
    assert body["data"]["pageSize"] == 10
    assert body["data"]["totalItems"] == 1


def test_admin_list_routes_accept_status_filters(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _mock_list_users(page, page_size, db, search=None, status=None, is_alumni=None):
        captured["users"] = status
        captured["users_is_alumni"] = is_alumni
        return {"items": [], "page": page or 1, "pageSize": page_size or 10, "totalItems": 0, "totalPages": 0}

    async def _mock_list_moderators(page, page_size, db, search=None, status=None):
        captured["moderators"] = status
        return {"items": [], "page": page or 1, "pageSize": page_size or 10, "totalItems": 0, "totalPages": 0}

    async def _mock_list_viewers(page, page_size, db, search=None, status=None):
        captured["viewers"] = status
        return {"items": [], "page": page or 1, "pageSize": page_size or 10, "totalItems": 0, "totalPages": 0}

    monkeypatch.setattr(admin_routes.services, "list_users", _mock_list_users)
    monkeypatch.setattr(admin_routes.services, "list_moderators", _mock_list_moderators)
    monkeypatch.setattr(admin_routes.services, "list_viewer", _mock_list_viewers)

    assert client.get("/api/v1/users", params={"status": "Pending"}).status_code == 200
    assert client.get("/api/v1/users", params={"status": "Active"}).status_code == 200
    assert client.get("/api/v1/users", params={"status": "Suspended"}).status_code == 200
    assert client.get("/api/v1/users", params={"status": "Banned"}).status_code == 200
    assert client.get("/api/v1/users", params={"status": "Deleted"}).status_code == 200
    invalid_users = client.get("/api/v1/users", params={"status": "Archived"})
    assert invalid_users.status_code == 200
    assert invalid_users.json()["status"] is False

    assert client.get("/api/v1/moderators", params={"status": "Active"}).status_code == 200
    assert client.get("/api/v1/moderators", params={"status": "Deleted"}).status_code == 200
    invalid_moderators = client.get("/api/v1/moderators", params={"status": "Suspended"})
    assert invalid_moderators.status_code == 200
    assert invalid_moderators.json()["status"] is False

    assert client.get("/api/v1/viewers", params={"status": "Active"}).status_code == 200
    assert client.get("/api/v1/viewers", params={"status": "Deleted"}).status_code == 200
    invalid_viewers = client.get("/api/v1/viewers", params={"status": "Banned"})
    assert invalid_viewers.status_code == 200
    assert invalid_viewers.json()["status"] is False

    assert captured["users"].value == "Deleted"
    assert captured["moderators"].value == "Deleted"
    assert captured["viewers"].value == "Deleted"


def test_admin_list_users_accepts_is_alumni_filter(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _mock_list_users(page, page_size, db, search=None, status=None, is_alumni=None):
        captured["is_alumni"] = is_alumni
        return {"items": [], "page": page or 1, "pageSize": page_size or 10, "totalItems": 0, "totalPages": 0}

    monkeypatch.setattr(admin_routes.services, "list_users", _mock_list_users)

    assert client.get("/api/v1/users", params={"is_alumni": "true"}).status_code == 200
    assert captured["is_alumni"] is True

    assert client.get("/api/v1/users", params={"is_alumni": "false"}).status_code == 200
    assert captured["is_alumni"] is False


def test_admin_user_routes_use_users_path(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "admin_get_user", _get_user)
    monkeypatch.setattr(admin_routes.services, "admin_delete_users", _delete_users)

    user_id = "11111111-1111-1111-1111-111111111111"
    get_response = client.get(f"/api/v1/users/{user_id}")
    delete_response = client.request(
        "DELETE",
        "/api/v1/users/",
        json={"userIds": [user_id], "role": "user"},
    )

    assert get_response.status_code == 200
    assert get_response.json()["data"]["found"] is True
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["deleted_users"][0]["user"]["userId"] == user_id


def test_admin_update_user_status(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "admin_update_user_status", _update_status)

    user_id = "11111111-1111-1111-1111-111111111111"
    suspend_response = client.patch(
        f"/api/v1/users/{user_id}/status",
        json={"status": "suspended", "note": "Spam / harassment"},
    )
    ban_response = client.patch(
        f"/api/v1/users/{user_id}/status",
        json={"status": "banned", "note": "Repeated abuse"},
    )
    active_response = client.patch(
        f"/api/v1/users/{user_id}/status",
        json={"status": "active"},
    )

    assert suspend_response.status_code == 200
    assert suspend_response.json()["data"]["status"] == "Suspended"
    assert ban_response.status_code == 200
    assert ban_response.json()["data"]["status"] == "Banned"
    assert active_response.status_code == 200
    assert active_response.json()["data"]["status"] == "Active"


def test_admin_update_user_status_note_is_optional(monkeypatch) -> None:
    monkeypatch.setattr(admin_routes.services, "admin_update_user_status", _update_status)

    user_id = "11111111-1111-1111-1111-111111111111"
    response = client.patch(
        f"/api/v1/users/{user_id}/status",
        json={"status": "suspended"},
    )
    assert response.status_code == 200
    assert response.json()["status"] is True
    assert response.json()["data"]["status"] == "Suspended"


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


def test_admin_create_user_route(monkeypatch) -> None:
    async def _mock_admin_create_user(payload, db, background_tasks=None, **_kwargs):
        role_name = payload.role.value if hasattr(payload.role, "value") else str(payload.role)
        return ApiResponse(
            status=True,
            message="User created successfully",
            data={
                "user": {
                    "email": payload.email,
                    "role": role_name,
                    "firstName": payload.firstName,
                    "lastName": payload.lastName,
                },
                "emailSent": True,
                "authProvider": "firebase" if role_name == "user" else "local",
            },
        )

    monkeypatch.setattr(admin_routes.services, "admin_create_user", _mock_admin_create_user)

    # 1. Test Firebase account creation flow for "user" role
    response_user = client.post(
        "/api/v1/admin/users",
        json={
            "firstName": "John",
            "lastName": "Doe",
            "email": "john.doe@example.com",
            "role": "user",
        },
    )
    assert response_user.status_code == 201
    body_user = response_user.json()
    assert body_user["status"] is True
    assert body_user["data"]["authProvider"] == "firebase"
    assert body_user["data"]["user"]["role"] == "user"

    # 2. Test Local account creation flow for "moderator" role
    response_mod = client.post(
        "/api/v1/admin/users",
        json={
            "firstName": "Jane",
            "lastName": "Smith",
            "email": "jane.smith@example.com",
            "role": "moderator",
        },
    )
    assert response_mod.status_code == 201
    body_mod = response_mod.json()
    assert body_mod["status"] is True
    assert body_mod["data"]["authProvider"] == "local"
    assert body_mod["data"]["user"]["role"] == "moderator"

    # 3. Test validation errors (e.g. invalid role)
    response_invalid_role = client.post(
        "/api/v1/admin/users",
        json={
            "firstName": "Alice",
            "lastName": "Brown",
            "email": "alice@example.com",
            "role": "superadmin",
        },
    )
    assert response_invalid_role.status_code == 200
    assert response_invalid_role.json()["status"] is False


def test_admin_edit_profile_route(monkeypatch) -> None:
    async def _mock_admin_edit_profile(user_id, payload, db):
        return ApiResponse(
            status=True,
            message="Profile updated successfully",
            data={
                "profile_photo_key": payload.profile_photo_key,
                "banner_photo_key": payload.banner_photo_key,
                "firstName": payload.firstName,
                "lastName": payload.lastName,
            },
        )

    monkeypatch.setattr(admin_routes.services, "admin_edit_profile", _mock_admin_edit_profile)

    response = client.patch(
        "/api/v1/update-profile",
        json={
            "firstName": "Super",
            "lastName": "Admin",
            "profile_photo_key": "new_profile.png",
            "banner_photo_key": "new_banner.png",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["firstName"] == "Super"
    assert body["data"]["profile_photo_key"] == "new_profile.png"
    assert body["data"]["banner_photo_key"] == "new_banner.png"


def test_update_user_profile_route(monkeypatch) -> None:
    from apps.profiles import services as profiles_services

    async def _mock_update_user_profile_by_admin_service(user_id, payload, db, **_kwargs):
        return {
            "userId": str(user_id),
            "firstName": payload.firstName,
            "lastName": payload.lastName,
            "email": payload.email,
            "major": payload.major,
            "minor": payload.minor,
            "university_id": payload.university_id,
            "education_level_id": payload.education_level_id,
            "academic_interests": payload.academic_interests,
            "profile_photo_key": payload.profile_photo_key,
            "banner_photo_key": payload.banner_photo_key,
            "bio": payload.bio,
        }

    monkeypatch.setattr(
        profiles_services,
        "update_user_profile_by_admin_service",
        _mock_update_user_profile_by_admin_service,
    )

    user_id = "22222222-2222-2222-2222-222222222222"
    response = client.patch(
        f"/api/v1/updateuserprofile?id={user_id}",
        json={
            "firstName": "Bob",
            "lastName": "Smith",
            "email": "bob.smith@example.com",
            "major": "Computer Science",
            "minor": "Math",
            "university_id": "33333333-3333-3333-3333-333333333333",
            "education_level_id": 1,
            "academic_interests": ["AI", "Programming"],
            "profile_photo_key": "bob_photo.png",
            "banner_photo_key": "bob_banner.png",
            "bio": "Hello world",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["userId"] == user_id
    assert body["data"]["firstName"] == "Bob"
    assert body["data"]["email"] == "bob.smith@example.com"
    assert body["data"]["major"] == "Computer Science"
    assert body["data"]["profile_photo_key"] == "bob_photo.png"
    assert body["data"]["banner_photo_key"] == "bob_banner.png"


def test_list_processing_posts_route(monkeypatch) -> None:
    async def _mock_list_processing_posts(_db, moderator_id=None, page=None, page_size=None):
        _ = moderator_id
        return {
            "items": [
                {
                    "user_id": "11111111-1111-1111-1111-111111111111",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "profile_photo_url": "/static/uploads/profiles/jane.jpg",
                    "post_id": "22222222-2222-2222-2222-222222222222",
                    "caption": "Hello",
                    "content_html": "<p>Hello</p>",
                    "media": [],
                    "is_moderator_reviewed": False,
                    "moderator_id": "22222222-2222-2222-2222-222222222222",
                    "moderator_name": "Mod Name",
                }
            ],
            "page": 1,
            "pageSize": 1,
            "totalItems": 1,
            "totalPages": 1,
        }

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_processing_posts_service", _mock_list_processing_posts)

    response = client.get("/api/v1/posts/processing", params={"page": 1, "pageSize": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Processing posts fetched successfully"
    assert body["data"]["items"][0]["first_name"] == "Jane"
    assert body["data"]["items"][0]["post_id"] == "22222222-2222-2222-2222-222222222222"
    assert body["data"]["items"][0]["is_moderator_reviewed"] is False
    assert body["data"]["items"][0]["moderator_name"] == "Mod Name"


def test_list_processing_posts_route_superadmin_with_moderator_id(monkeypatch) -> None:
    from uuid import UUID
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_admin

    called_moderator_id = None

    async def _mock_list_processing_posts(_db, moderator_id=None, page=None, page_size=None):
        nonlocal called_moderator_id
        called_moderator_id = moderator_id
        return {
            "items": [],
            "page": 1,
            "pageSize": 1,
            "totalItems": 0,
            "totalPages": 1,
        }

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_processing_posts_service", _mock_list_processing_posts)

    try:
        # Call with moderator_id query param
        target_uuid = "33333333-3333-3333-3333-333333333333"
        response = client.get("/api/v1/posts/processing", params={"moderator_id": target_uuid})
        assert response.status_code == 200
        assert called_moderator_id == UUID(target_uuid)

        # Call without moderator_id query param
        response = client.get("/api/v1/posts/processing")
        assert response.status_code == 200
        assert called_moderator_id is None
    finally:
        app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator


def test_list_processing_posts_route_moderator_ignores_moderator_id(monkeypatch) -> None:
    from uuid import UUID
    called_moderator_id = None

    async def _mock_list_processing_posts(_db, moderator_id=None, page=None, page_size=None):
        nonlocal called_moderator_id
        called_moderator_id = moderator_id
        return {
            "items": [],
            "page": 1,
            "pageSize": 1,
            "totalItems": 0,
            "totalPages": 1,
        }

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_processing_posts_service", _mock_list_processing_posts)

    # Call with moderator_id query param as a standard moderator.
    # It should use current_user.id ("22222222-2222-2222-2222-222222222222") instead of the passed target_uuid.
    target_uuid = "33333333-3333-3333-3333-333333333333"
    response = client.get("/api/v1/posts/processing", params={"moderator_id": target_uuid})
    assert response.status_code == 200
    assert str(called_moderator_id) == "22222222-2222-2222-2222-222222222222"


def test_list_reviewed_posts_route(monkeypatch) -> None:
    async def _mock_list_reviewed_posts(
        _db, moderator_id, status=None, page=None, page_size=None, viewer_user_id=None, search=None, sort=None, order=None
    ):
        _ = (moderator_id, status, page, page_size, viewer_user_id, search)
        return {
            "items": [
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "user_id": "11111111-1111-1111-1111-111111111111",
                    "caption": "Reviewed post",
                    "content_html": "<p>Reviewed</p>",
                    "status": "published",
                    "is_moderator_reviewed": True,
                    "reviewed_at": "2026-01-01T00:00:00+00:00",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "moderator_id": "33333333-3333-3333-3333-333333333333",
                    "moderator_name": "Jane Doe",
                    "profile_photo_url": "/static/uploads/profiles/jane.jpg",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "media": [],
                }
            ],
            "page": 1,
            "pageSize": 1,
            "totalItems": 1,
            "totalPages": 1,
        }

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_reviewed_posts_by_state_service", _mock_list_reviewed_posts)

    response = client.get("/api/v1/admin/posts/reviewed", params={"status": "published", "page": 1, "pageSize": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Posts fetched successfully"
    assert body["data"]["items"][0]["status"] == "published"


def test_list_reviewed_posts_route_passes_search(monkeypatch) -> None:
    captured = {}

    async def _mock_list_reviewed_posts(
        _db, moderator_id=None, status=None, page=None, page_size=None, viewer_user_id=None, search=None, sort=None, order=None
    ):
        captured["status"] = status
        captured["page"] = page
        captured["page_size"] = page_size
        captured["search"] = search
        captured["sort"] = sort
        return {"items": [], "page": 1, "pageSize": 9, "totalItems": 0, "totalPages": 0}

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_reviewed_posts_by_state_service", _mock_list_reviewed_posts)

    response = client.get(
        "/api/v1/admin/posts/reviewed",
        params={"status": "published", "page": 1, "pageSize": 9, "search": "jane"},
    )
    assert response.status_code == 200
    assert response.json()["status"] is True
    assert captured["status"] == "published"
    assert captured["page"] == 1
    assert captured["page_size"] == 9
    assert captured["search"] == "jane"


def test_list_reviewed_posts_route_passes_sort(monkeypatch) -> None:
    from common.enums import ReviewedPostOrder, ReviewedPostSort

    captured = {}

    async def _mock_list_reviewed_posts(
        _db, moderator_id=None, status=None, page=None, page_size=None, viewer_user_id=None, search=None, sort=None, order=None
    ):
        captured["sort"] = sort
        captured["order"] = order
        return {"items": [], "page": 1, "pageSize": 9, "totalItems": 0, "totalPages": 0}

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_reviewed_posts_by_state_service", _mock_list_reviewed_posts)

    response = client.get(
        "/api/v1/admin/posts/reviewed",
        params={"status": "flagged", "sort": "updated_at", "order": "desc"},
    )
    assert response.status_code == 200
    assert response.json()["status"] is True
    assert captured["sort"] == ReviewedPostSort.updated_at
    assert captured["order"] == ReviewedPostOrder.desc


def test_list_reviewed_posts_route_passes_order_asc(monkeypatch) -> None:
    from common.enums import ReviewedPostOrder, ReviewedPostSort

    captured = {}

    async def _mock_list_reviewed_posts(
        _db, moderator_id=None, status=None, page=None, page_size=None, viewer_user_id=None, search=None, sort=None, order=None
    ):
        captured["sort"] = sort
        captured["order"] = order
        return {"items": [], "page": 1, "pageSize": 9, "totalItems": 0, "totalPages": 0}

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_reviewed_posts_by_state_service", _mock_list_reviewed_posts)

    response = client.get(
        "/api/v1/admin/posts/reviewed",
        params={"status": "published", "sort": "created_at", "order": "asc"},
    )
    assert response.status_code == 200
    assert captured["sort"] == ReviewedPostSort.created_at
    assert captured["order"] == ReviewedPostOrder.asc


def test_list_processing_posts_route_viewer(monkeypatch) -> None:
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_viewer

    called_moderator_id = None

    async def _mock_list_processing_posts(_db, moderator_id=None, page=None, page_size=None):
        nonlocal called_moderator_id
        called_moderator_id = moderator_id
        return {
            "items": [],
            "page": 1,
            "pageSize": 1,
            "totalItems": 0,
            "totalPages": 1,
        }

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_processing_posts_service", _mock_list_processing_posts)

    try:
        response = client.get("/api/v1/posts/processing")
        assert response.status_code == 200
        # Viewer is not restricted to their own moderator ID; called_moderator_id should be None
        assert called_moderator_id is None
    finally:
        app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator


def test_list_reviewed_posts_route_without_moderator_filter(monkeypatch) -> None:
    called_moderator_id = "sentinel"

    async def _mock_list_reviewed_posts(
        _db, moderator_id=None, status=None, page=None, page_size=None, viewer_user_id=None, search=None, sort=None, order=None
    ):
        nonlocal called_moderator_id
        _ = (viewer_user_id, search, sort)
        called_moderator_id = moderator_id
        return {
            "items": [],
            "page": 1,
            "pageSize": 1,
            "totalItems": 0,
            "totalPages": 1,
        }

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_reviewed_posts_by_state_service", _mock_list_reviewed_posts)

    response = client.get("/api/v1/admin/posts/reviewed")
    assert response.status_code == 200
    # Without moderator_id query param, service is called with moderator_id=None.
    assert called_moderator_id is None


def test_list_users_route_viewer(monkeypatch) -> None:
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_viewer
    monkeypatch.setattr(admin_routes.services, "list_users", _list_users)
    try:
        response = client.get("/api/v1/users", params={"page": 1, "pageSize": 10})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
    finally:
        app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator


def test_get_user_route_viewer(monkeypatch) -> None:
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_viewer
    monkeypatch.setattr(admin_routes.services, "admin_get_user", _get_user)
    user_id = "11111111-1111-1111-1111-111111111111"
    try:
        response = client.get(f"/api/v1/users/{user_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        assert body["data"]["userId"] == user_id
    finally:
        app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator


def test_list_reviewed_posts_route_viewer(monkeypatch) -> None:
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_viewer

    async def _mock_list_reviewed_posts(
        _db, moderator_id=None, status=None, page=None, page_size=None, viewer_user_id=None, search=None, sort=None, order=None
    ):
        _ = (moderator_id, status, page, page_size, viewer_user_id, search, sort)
        return {"items": [], "page": 1, "pageSize": 10, "totalItems": 0, "totalPages": 0}

    import apps.feed.services as feed_services
    monkeypatch.setattr(feed_services, "list_reviewed_posts_by_state_service", _mock_list_reviewed_posts)
    try:
        response = client.get("/api/v1/admin/posts/reviewed", params={"status": "published"})
        assert response.status_code == 200
        assert response.json()["status"] is True
    finally:
        app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator


def test_list_moderators_route_viewer(monkeypatch) -> None:
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_viewer

    async def _mock_list_moderators(page, page_size, db, search=None, status=None):
        _ = (page, page_size, db, search, status)
        return {"items": [], "page": 1, "pageSize": 10, "totalItems": 0, "totalPages": 0}

    monkeypatch.setattr(admin_routes.services, "list_moderators", _mock_list_moderators)
    try:
        response = client.get("/api/v1/moderators")
        assert response.status_code == 200
        assert response.json()["status"] is True
    finally:
        app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator


def test_list_feature_flags_route(monkeypatch) -> None:
    async def _mock_list(_db):
        return {
            "items": [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "key": "chat",
                    "name": "Chat",
                    "description": "In-app messaging",
                    "is_enabled": True,
                    "created_at": "2026-08-10T12:00:00Z",
                    "updated_at": "2026-08-10T12:00:00Z",
                }
            ]
        }

    monkeypatch.setattr(admin_routes.services, "list_feature_flags", _mock_list)
    response = client.get("/api/v1/feature-flags")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["items"][0]["key"] == "chat"


def test_admin_patch_feature_flag_route(monkeypatch) -> None:
    async def _mock_update(payload, _db, **_kwargs):
        return {
            "id": "11111111-1111-1111-1111-111111111111",
            "key": payload.key,
            "name": "Chat",
            "description": "msg",
            "is_enabled": payload.is_enabled,
            "created_at": "2026-08-10T12:00:00Z",
            "updated_at": "2026-08-10T12:00:00Z",
        }

    monkeypatch.setattr(admin_routes.services, "update_feature_flag", _mock_update)
    response = client.patch(
        "/api/v1/admin/feature-flags",
        json={"feature_key": "chat", "enabled": False},
    )
    assert response.status_code == 200
    assert response.json()["data"]["is_enabled"] is False


def test_admin_create_feature_flag_route(monkeypatch) -> None:
    async def _mock_create(payload, _db, **_kwargs):
        return {
            "id": "22222222-2222-2222-2222-222222222222",
            "key": payload.key,
            "name": payload.name,
            "description": payload.description,
            "is_enabled": payload.is_enabled,
            "created_at": "2026-08-10T12:00:00Z",
            "updated_at": "2026-08-10T12:00:00Z",
        }

    monkeypatch.setattr(admin_routes.services, "create_feature_flag", _mock_create)
    response = client.post(
        "/api/v1/admin/feature-flags",
        json={
            "key": "stories",
            "feature_key": "stories",
            "name": "Stories",
            "description": "Short-lived stories feed in the mobile app.",
            "is_enabled": True,
            "enabled": True,
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["key"] == "stories"


def test_admin_delete_feature_flag_route(monkeypatch) -> None:
    flag_id = "22222222-2222-2222-2222-222222222222"

    async def _mock_delete(received_id, _db, **_kwargs):
        assert str(received_id) == flag_id
        return {
            "id": flag_id,
            "key": "stories",
            "name": "Stories",
            "description": "Short-lived stories",
            "is_enabled": True,
            "created_at": "2026-08-10T12:00:00Z",
            "updated_at": "2026-08-10T12:00:00Z",
        }

    monkeypatch.setattr(admin_routes.services, "delete_feature_flag", _mock_delete)
    response = client.delete(f"/api/v1/admin/feature-flags/{flag_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Feature flag deleted successfully"
    assert body["data"]["id"] == flag_id
    assert body["data"]["key"] == "stories"


def test_admin_feature_flags_route_viewer(monkeypatch) -> None:
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_viewer

    async def _mock_update(payload, _db, **_kwargs):
        return {
            "id": "11111111-1111-1111-1111-111111111111",
            "key": payload.key,
            "name": "Chat",
            "description": "msg",
            "is_enabled": payload.is_enabled,
            "created_at": "2026-08-10T12:00:00Z",
            "updated_at": "2026-08-10T12:00:00Z",
        }

    monkeypatch.setattr(admin_routes.services, "update_feature_flag", _mock_update)
    try:
        response = client.patch(
            "/api/v1/admin/feature-flags",
            json={"feature_key": "chat", "enabled": False},
        )
        assert response.status_code == 200
        assert response.json()["status"] is True
    finally:
        app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator


def test_create_user_route_viewer(monkeypatch) -> None:
    app.dependency_overrides[get_current_moderator_or_viewer] = _override_viewer

    async def _mock_admin_create_user(payload, _db, _background_tasks, **_kwargs):
        return ApiResponse(status=True, message="User created successfully", data={})

    monkeypatch.setattr(admin_routes.services, "admin_create_user", _mock_admin_create_user)
    try:
        response = client.post(
            "/api/v1/admin/users",
            json={
                "firstName": "View",
                "lastName": "Er",
                "email": "viewer.create@example.com",
                "role": "user",
            },
        )
        assert response.status_code == 201
        assert response.json()["status"] is True
    finally:
        app.dependency_overrides[get_current_moderator_or_viewer] = _override_moderator

