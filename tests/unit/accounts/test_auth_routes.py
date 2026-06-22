<<<<<<< HEAD
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
        referenceCode="",
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
    
    assert body["data"]["user"]["invitationCode"] is None
    assert body["data"]["user"]["referenceCode"] == ""
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
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
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
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
        },
    )
    assert response.status_code == 422


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
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
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
            "firebaseId": "valid-firebase-id-token",
            "device_id": "test-device-id",
        },
    )
    assert response.status_code == 422


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

    assert response.status_code == 201
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

    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "ACCOUNT_EXISTS"
    assert body["registration_type"] == "email"


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

    assert response.status_code == 201
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Signup successful"
=======
"""
Unit tests for apps/accounts/schemas.py
Covers: EmailSignupRequest, LoginRequest, ResetPasswordRequest validators
and utility schemas (ApiResponse, OtpVerifyRequest, RefreshTokenRequest, etc.)
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.accounts.schemas import (
    ApiResponse,
    AuthSessionResponse,
    AuthUserResponse,
    EmailSignupRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    NotificationPreferences,
    OtpVerifyRequest,
    PaginationParams,
    RefreshTokenRequest,
    ResendOtpRequest,
    ResetPasswordRequest,
    SocialAuthRequest,
    TokenResponse,
)
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# ApiResponse
# ---------------------------------------------------------------------------

class TestApiResponse:
    def test_default_values(self):
        r = ApiResponse()
        assert r.status is True
        assert r.message == "success"
        assert r.data is None

    def test_custom_values(self):
        r = ApiResponse(status=False, message="error", data={"key": "val"})
        assert r.status is False
        assert r.message == "error"
        assert r.data == {"key": "val"}


# ---------------------------------------------------------------------------
# EmailSignupRequest
# ---------------------------------------------------------------------------

class TestEmailSignupRequest:
    def _valid(self, **kwargs):
        defaults = dict(
            firstName="John",
            lastName="Doe",
            email="pytest.user@example.com",
            password="Secret123",
        )
        defaults.update(kwargs)
        return EmailSignupRequest(**defaults)

    def test_valid_signup(self):
        req = self._valid()
        assert req.firstName == "John"
        assert req.email == "pytest.user@example.com"
        assert req.role == "user"

    def test_email_normalised_to_lowercase(self):
        req = self._valid(email="PYTEST.USER@Example.COM")
        assert req.email == "pytest.user@example.com"

    def test_blank_first_name_raises(self):
        with pytest.raises(ValidationError, match="names cannot be blank"):
            self._valid(firstName="   ")

    def test_blank_last_name_raises(self):
        with pytest.raises(ValidationError, match="names cannot be blank"):
            self._valid(lastName="")

    def test_blank_email_raises(self):
        with pytest.raises(ValidationError):
            self._valid(email="not-an-email")

    def test_password_too_short_raises(self):
        with pytest.raises(ValidationError):
            self._valid(password="Ab1")

    def test_password_no_uppercase_raises(self):
        with pytest.raises(ValidationError, match="uppercase"):
            self._valid(password="secret123")

    def test_password_no_digit_raises(self):
        with pytest.raises(ValidationError, match="number"):
            self._valid(password="SecretPass")

    def test_first_name_stripped(self):
        req = self._valid(firstName="  Alice  ")
        assert req.firstName == "Alice"


# ---------------------------------------------------------------------------
# LoginRequest
# ---------------------------------------------------------------------------

class TestLoginRequest:
    def _valid(self, **kwargs):
        defaults = dict(email="pytest.login@example.com", password="Secret123")
        defaults.update(kwargs)
        return LoginRequest(**defaults)

    def test_valid_login(self):
        req = self._valid()
        assert req.email == "pytest.login@example.com"

    def test_email_normalised(self):
        req = self._valid(email="PYTEST.LOGIN@EXAMPLE.COM")
        assert req.email == "pytest.login@example.com"

    def test_blank_password_raises(self):
        with pytest.raises(ValidationError):
            self._valid(password="   ")

    def test_invalid_email_raises(self):
        with pytest.raises(ValidationError):
            self._valid(email="not-valid")


# ---------------------------------------------------------------------------
# ResetPasswordRequest
# ---------------------------------------------------------------------------

class TestResetPasswordRequest:
    def test_valid_reset(self):
        req = ResetPasswordRequest(token="tok123", new_password="NewPass1")
        assert req.token == "tok123"

    def test_no_uppercase_raises(self):
        with pytest.raises(ValidationError, match="uppercase"):
            ResetPasswordRequest(token="tok", new_password="newpass1")

    def test_no_digit_raises(self):
        with pytest.raises(ValidationError, match="number"):
            ResetPasswordRequest(token="tok", new_password="NewPassWord")


# ---------------------------------------------------------------------------
# OtpVerifyRequest
# ---------------------------------------------------------------------------

class TestOtpVerifyRequest:
    def test_valid(self):
        req = OtpVerifyRequest(email="pytest@example.com", otp="123456")
        assert req.otp == "123456"


# ---------------------------------------------------------------------------
# ResendOtpRequest
# ---------------------------------------------------------------------------

class TestResendOtpRequest:
    def test_valid(self):
        req = ResendOtpRequest(email="pytest@example.com")
        assert req.email == "pytest@example.com"


# ---------------------------------------------------------------------------
# ForgotPasswordRequest
# ---------------------------------------------------------------------------

class TestForgotPasswordRequest:
    def test_valid(self):
        req = ForgotPasswordRequest(email="pytest@example.com")
        assert req.email == "pytest@example.com"

    def test_invalid_email_raises(self):
        with pytest.raises(ValidationError):
            ForgotPasswordRequest(email="bad-email")


# ---------------------------------------------------------------------------
# RefreshTokenRequest
# ---------------------------------------------------------------------------

class TestRefreshTokenRequest:
    def test_valid(self):
        req = RefreshTokenRequest(refreshToken="some.jwt.token")
        assert req.refreshToken == "some.jwt.token"


# ---------------------------------------------------------------------------
# LogoutRequest
# ---------------------------------------------------------------------------

class TestLogoutRequest:
    def test_all_optional(self):
        req = LogoutRequest()
        assert req.accessToken is None
        assert req.refreshToken is None

    def test_with_tokens(self):
        req = LogoutRequest(accessToken="a", refreshToken="r")
        assert req.accessToken == "a"
        assert req.refreshToken == "r"


# ---------------------------------------------------------------------------
# NotificationPreferences
# ---------------------------------------------------------------------------

class TestNotificationPreferences:
    def test_defaults(self):
        prefs = NotificationPreferences()
        assert prefs.email is True
        assert prefs.push is True
        assert prefs.inApp is True


# ---------------------------------------------------------------------------
# PaginationParams
# ---------------------------------------------------------------------------

class TestPaginationParams:
    def test_defaults(self):
        p = PaginationParams()
        assert p.page == 1
        assert p.pageSize == 20


# ---------------------------------------------------------------------------
# SocialAuthRequest
# ---------------------------------------------------------------------------

class TestSocialAuthRequest:
    def test_valid_google(self):
        req = SocialAuthRequest(provider="google", idToken="tok")
        assert req.provider == "google"

    def test_valid_apple(self):
        req = SocialAuthRequest(provider="apple", idToken="tok")
        assert req.provider == "apple"
>>>>>>> 5038703 (Test cases)
