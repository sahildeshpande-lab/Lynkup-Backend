from __future__ import annotations

from datetime import datetime
from fastapi.testclient import TestClient

from entrypoints.api import app
from core.database.session import get_session
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
    from core.auth.firebase import get_current_firebase_user, get_firebase_user_from_payload
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_firebase_user] = _mock_firebase_user
    app.dependency_overrides[get_firebase_user_from_payload] = _mock_firebase_user


def teardown_module() -> None:
    from core.auth.firebase import get_current_firebase_user, get_firebase_user_from_payload
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_firebase_user, None)
    app.dependency_overrides.pop(get_firebase_user_from_payload, None)


async def _mock_signup(payload, firebase_user, db) -> ApiResponse:
    from apps.accounts.schemas import UserBaseResponse
    user = UserBaseResponse(
        id="jane-doe-id",
        firebase_uid="test-firebase-uid",
        firebaseuid="test-firebase-uid",
        firstName=payload.firstName,
        lastName=payload.lastName,
        email=payload.email,
        role=payload.role,
        createdAt=datetime.now(),
        updatedAt=datetime.now(),
        status="pending",
        profileVisibility="public",
        email_verified_at=None,
        is_onboarding_completed=False,
    )
    return ApiResponse(
        status=True,
        message="Signup successful",
        data={
            "user": user.model_dump(),
            "emailSent": True,
            "firebaseuid": "test-firebase-uid",
        }
    )


async def _mock_login(payload, firebase_user, db) -> ApiResponse:
    from apps.accounts.schemas import UserBaseResponse
    user = UserBaseResponse(
        id="jane-doe-id",
        firebase_uid="test-firebase-uid",
        firebaseuid="test-firebase-uid",
        firstName="",
        lastName="",
        email=firebase_user["email"].lower(),
        role="user",
        createdAt=datetime.now(),
        updatedAt=datetime.now(),
    )
    return ApiResponse(
        status=True,
        message="Login successful",
        data={
            "user": user.model_dump(),
            "emailSent": False,
            "firebaseuid": "test-firebase-uid",
        }
    )


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
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Signup successful"
    assert body["data"]["emailSent"] is True
    assert body["data"]["user"]["status"] == "pending"
    assert body["data"]["user"]["profileVisibility"] == "public"
    assert body["data"]["user"]["email_verified_at"] is None
    assert body["data"]["user"]["is_onboarding_completed"] is False


def test_login_route_uses_login_request_schema(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "login", _mock_login)

    async def _mock_firebase_login_user():
        return {"uid": "test-firebase-uid", "email": "USER@Example.com"}

    from core.auth.firebase import get_current_firebase_user, get_firebase_user_from_payload
    app.dependency_overrides[get_current_firebase_user] = _mock_firebase_login_user
    app.dependency_overrides[get_firebase_user_from_payload] = _mock_firebase_login_user

    try:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "USER@Example.com",
                "password": "stringsqq111AA@2t",
                "firebaseId": "valid-firebase-id-token",
                "device_id": "test-device-id",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        assert body["message"] == "Login successful"
        assert body["data"]["user"]["email"] == "user@example.com"
    finally:
        app.dependency_overrides[get_current_firebase_user] = _mock_firebase_user
        app.dependency_overrides[get_firebase_user_from_payload] = _mock_firebase_user


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
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False

    # Test blank lastName
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "firstName": "Jane",
            "lastName": "",
            "email": "jane@example.com",
            "password": "Secret123",
            "role": "user",
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False


def test_login_route_rejects_blank_fields(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "login", _mock_login)

    # Test blank password
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "user@example.com",
            "password": "       ",
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False


async def _mock_forgot_password(payload, db) -> ApiResponse:
    return ApiResponse(status=True, message="Password reset link sent successfully")


async def _mock_reset_password(payload, db) -> ApiResponse:
    return ApiResponse(status=True, message="Password reset successful")


def test_forgot_password_route(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "forgot_password", _mock_forgot_password)

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "jane@example.com", "firebaseId": "valid-firebase-id-token"},
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
            "firebaseId": "valid-firebase-id-token",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Password reset successful"


def test_login_route_uses_login_request_schema(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "login", _mock_login)

    async def _mock_firebase_login_user():
        return {"uid": "test-firebase-uid", "email": "USER@Example.com"}

    from core.auth.firebase import get_current_firebase_user, get_firebase_user_from_payload
    app.dependency_overrides[get_current_firebase_user] = _mock_firebase_login_user
    app.dependency_overrides[get_firebase_user_from_payload] = _mock_firebase_login_user

    try:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "USER@Example.com",
                "password": "stringsqq111AA@2t",
                "firebaseId": "valid-firebase-id-token",
                "device_id": "test-device-id",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] is True
        assert body["message"] == "Login successful"
        assert body["data"]["user"]["email"] == "user@example.com"
    finally:
        app.dependency_overrides[get_current_firebase_user] = _mock_firebase_user
        app.dependency_overrides[get_firebase_user_from_payload] = _mock_firebase_user


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
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False

    # Test blank lastName
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "firstName": "Jane",
            "lastName": "",
            "email": "jane@example.com",
            "password": "Secret123",
            "role": "user",
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False


def test_login_route_rejects_blank_fields(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "login", _mock_login)

    # Test blank password
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "user@example.com",
            "password": "       ",
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is False


async def _mock_forgot_password(payload, db) -> ApiResponse:
    return ApiResponse(status=True, message="Password reset link sent successfully")


async def _mock_reset_password(payload, db) -> ApiResponse:
    return ApiResponse(status=True, message="Password reset successful")


def test_forgot_password_route(monkeypatch) -> None:
    monkeypatch.setattr(auth_routes.services, "forgot_password", _mock_forgot_password)

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "jane@example.com", "firebaseId": "valid-firebase-id-token"},
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
            "firebaseId": "valid-firebase-id-token",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Password reset successful"


def test_social_auth_route_login_success(monkeypatch) -> None:
    async def _mock_social_auth(payload, db):
        return {
            "accessToken": "access_token_123",
            "refreshToken": "refresh_token_123",
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

    monkeypatch.setattr(auth_routes, "social_auth_service", _mock_social_auth)

    response = client.post(
        "/api/v1/auth/social",
        json={"provider": "google", "idToken": "google_test_token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Login successful"
    assert body["data"]["accessToken"] == "access_token_123"
    assert body["data"]["user"]["email"] == "jane@example.com"


def test_social_auth_route_signup_success(monkeypatch) -> None:
    async def _mock_social_auth(payload, db):
        return {
            "accessToken": "access_token_123",
            "refreshToken": "refresh_token_123",
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

    monkeypatch.setattr(auth_routes, "social_auth_service", _mock_social_auth)

    response = client.post(
        "/api/v1/auth/social",
        json={"provider": "google", "idToken": "google_test_token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Signup successful"
    assert body["data"]["accessToken"] == "access_token_123"
    assert body["data"]["user"]["email"] == "jane@example.com"


def test_social_auth_route_conflict(monkeypatch) -> None:
    from apps.accounts.services import AccountExistsException
    async def _mock_social_auth(payload, db):
        raise AccountExistsException(registration_type="email")

    monkeypatch.setattr(auth_routes, "social_auth_service", _mock_social_auth)

    response = client.post(
        "/api/v1/auth/social",
        json={"provider": "google", "idToken": "google_test_token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Account already exists. Please login using your registered method."
    assert body["data"] is None


def test_social_auth_route_profile_photo_url(monkeypatch) -> None:
    async def _mock_social_auth(payload, db):
        assert payload.profilePhotoUrl == "profiles/photo.png"
        return {
            "accessToken": "access_token_123",
            "refreshToken": "refresh_token_123",
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

    monkeypatch.setattr(auth_routes, "social_auth_service", _mock_social_auth)

    response = client.post(
        "/api/v1/auth/social",
        json={
            "provider": "google",
            "idToken": "google_test_token",
            "profilePhotoUrl": "profiles/photo.png"
        }
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Signup successful"
