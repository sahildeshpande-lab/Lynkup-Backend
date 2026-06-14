from __future__ import annotations

from datetime import datetime
from fastapi.testclient import TestClient

from entrypoints.api import app
from core.db.session import get_session
from apps.accounts import routes as auth_routes
from apps.accounts.schemas import ApiResponse, AuthSessionResponse, AuthUserResponse

client = TestClient(app)


class _NoopSession:
    pass


async def _override_session():
    yield _NoopSession()


async def _mock_firebase_user():
    return {"uid": "test-firebase-uid", "email": "jane@example.com"}


def setup_module() -> None:
    from core.auth.firebase import get_current_firebase_user
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_firebase_user] = _mock_firebase_user


def teardown_module() -> None:
    from core.auth.firebase import get_current_firebase_user
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_firebase_user, None)


async def _mock_signup(payload, firebase_user, db) -> ApiResponse:
    user = AuthUserResponse(
        id="jane-doe-id",
        firebase_uid="test-firebase-uid",
        firstName=payload.firstName,
        lastName=payload.lastName,
        email=payload.email,
        role=payload.role,
        createdAt=datetime.now(),
        updatedAt=datetime.now(),
        referenceCode="",
        status="pending",
        profileVisibility="public",
        email_verified_at=None,
        is_onboarding=True,
    )
    auth_session = AuthSessionResponse(
        user=user,
        emailSent=True,
    )
    return ApiResponse(status=True, message="Signup successful", data=auth_session.model_dump())


async def _mock_login(firebase_user, db) -> ApiResponse:
    user = AuthUserResponse(
        id="jane-doe-id",
        firebase_uid="test-firebase-uid",
        firstName="",
        lastName="",
        email=firebase_user["email"].lower(),
        role="user",
        createdAt=datetime.now(),
        updatedAt=datetime.now(),
    )
    auth_session = AuthSessionResponse(
        user=user,
        emailSent=False,
    )
    return ApiResponse(status=True, message="Login successful", data=auth_session.model_dump())


def test_signup_route_exists(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "signup", _mock_signup)

    response = client.post(
        "/api/v1/auth/signup",
        json={
            "firstName": "Jane",
            "lastName": "Doe",
            "email": "jane@example.com",
            "password": "Secret123",
            "role": "user",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Signup successful"
    assert body["data"]["emailSent"] is True
    
    assert body["data"]["user"]["invitationCode"] is None
    assert body["data"]["user"]["referenceCode"] == ""
    assert body["data"]["user"]["status"] == "pending"
    assert body["data"]["user"]["profileVisibility"] == "public"
    assert body["data"]["user"]["email_verified_at"] is None
    assert body["data"]["user"]["is_onboarding"] is True


def test_login_route_uses_login_request_schema(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "login", _mock_login)

    async def _mock_firebase_login_user():
        return {"uid": "test-firebase-uid", "email": "USER@Example.com"}

    from core.auth.firebase import get_current_firebase_user
    app.dependency_overrides[get_current_firebase_user] = _mock_firebase_login_user

    try:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "USER@Example.com",
                "password": "stringsqq111AA@2t",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        assert body["message"] == "Login successful"
        assert body["data"]["user"]["email"] == "user@example.com"
    finally:
        app.dependency_overrides[get_current_firebase_user] = _mock_firebase_user


def test_signup_route_rejects_blank_fields(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "signup", _mock_signup)

    # Test blank firstName
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "firstName": "  ",
            "lastName": "Doe",
            "email": "jane@example.com",
            "password": "Secret123",
            "role": "user",
        },
    )
    assert response.status_code == 422

    # Test blank lastName
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "firstName": "Jane",
            "lastName": "",
            "email": "jane@example.com",
            "password": "Secret123",
            "role": "user",
        },
    )
    assert response.status_code == 422


def test_login_route_rejects_blank_fields(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "login", _mock_login)

    # Test blank password
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "user@example.com",
            "password": "       ",
        },
    )
    assert response.status_code == 422


def test_refresh_route_returns_user_base_payload(monkeypatch) -> None:
    async def _mock_refresh_token(payload, db):
        return {
            "refreshToken": payload.refreshToken,
            "user": {
                "id": "jane-doe-id",
                "firebase_uid": "test-firebase-uid",
                "firstName": "",
                "lastName": "",
                "email": "user@example.com",
                "role": "user",
                "createdAt": datetime.now(),
                "updatedAt": datetime.now(),
            }
        }

    monkeypatch.setattr(auth_routes.services, "refresh_token", _mock_refresh_token)

    response = client.post(
        "/api/v1/auth/refresh",
        json={
            "refreshToken": "refresh_user@example.com",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "token refreshed"
    assert body["data"]["refreshToken"] == "refresh_user@example.com"
    assert body["data"]["user"]["email"] == "user@example.com"


async def _mock_forgot_password(payload, db) -> ApiResponse:
    return ApiResponse(status=True, message="Password reset link sent successfully")


async def _mock_reset_password(payload, db) -> ApiResponse:
    return ApiResponse(status=True, message="Password reset successful")


def test_forgot_password_route(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "forgot_password", _mock_forgot_password)

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "user@example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Password reset link sent successfully"


def test_reset_password_route(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "reset_password", _mock_reset_password)

    response = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": "token-123",
            "new_password": "NewPassword@123",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Password reset successful"


def test_social_auth_route_login_success(monkeypatch) -> None:
    async def _mock_social_auth_bearer(id_token, db):
        return {
            "access_token": "access_token_123",
            "refresh_token": "refresh_token_123",
            "user": {
                "id": "user-id-123",
                "firstName": "Jane",
                "lastName": "Doe",
                "email": "jane@example.com",
                "role": "user",
                "createdAt": datetime.now().isoformat(),
                "updatedAt": datetime.now().isoformat(),
            },
        }, False

    monkeypatch.setattr(auth_routes, "social_auth_bearer", _mock_social_auth_bearer)

    response = client.post(
        "/api/v1/auth/social",
        headers={"Authorization": "Bearer google_test_token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Login successful"
    assert body["data"]["access_token"] == "access_token_123"
    assert body["data"]["user"]["email"] == "jane@example.com"


def test_social_auth_route_signup_success(monkeypatch) -> None:
    async def _mock_social_auth_bearer(id_token, db):
        return {
            "access_token": "access_token_123",
            "refresh_token": "refresh_token_123",
            "user": {
                "id": "user-id-123",
                "firstName": "Jane",
                "lastName": "Doe",
                "email": "jane@example.com",
                "role": "user",
                "createdAt": datetime.now().isoformat(),
                "updatedAt": datetime.now().isoformat(),
            },
        }, True

    monkeypatch.setattr(auth_routes, "social_auth_bearer", _mock_social_auth_bearer)

    response = client.post(
        "/api/v1/auth/social",
        headers={"Authorization": "Bearer google_test_token"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Signup successful"
    assert body["data"]["access_token"] == "access_token_123"
    assert body["data"]["user"]["email"] == "jane@example.com"


def test_social_auth_route_conflict(monkeypatch) -> None:
    from apps.accounts.services import AccountExistsException
    async def _mock_social_auth_bearer(id_token, db):
        raise AccountExistsException(registration_type="email")

    monkeypatch.setattr(auth_routes, "social_auth_bearer", _mock_social_auth_bearer)

    response = client.post(
        "/api/v1/auth/social",
        headers={"Authorization": "Bearer google_test_token"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "ACCOUNT_EXISTS"
    assert body["registration_type"] == "email"

