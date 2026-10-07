"""Thin integration tests: disposable-email gate on registration call sites."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest

from apps.accounts.schemas import EmailSignupRequest, SocialAuthRequest
from apps.accounts.services import registration_service as reg_svc
from apps.administration.schemas import AdminSignupRequest, AdminUserCreateRequest
from apps.administration.services import auth_service as admin_auth
from apps.administration.services.user_management_service import admin_create_user
from common.email_validation import DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE
from common.exceptions import ApiError
from common.exceptions import ApiError
from tests.unit.conftest import FakeScalarResult


def _auth_settings(*, enabled: bool) -> MagicMock:
    return MagicMock(is_disposable_email_enabled=enabled)


@pytest.mark.asyncio
async def test_signup_rejects_disposable_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        reg_svc,
        "auth_settings",
        _auth_settings(enabled=True),
    )
    db = Mock()
    db.rollback = AsyncMock()
    db.execute = AsyncMock(return_value=FakeScalarResult(None))
    payload = EmailSignupRequest(
        firstName="Jane",
        lastName="Doe",
        email="temp@mailinator.com",
        password="Secret123",
        role="user",
        firebaseId="token",
    )
    with (
        patch(
            "apps.accounts.services.registration_service.delete_firebase_user_safely",
            Mock(),
        ),
        pytest.raises(ApiError, match=DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE),
    ):
        await reg_svc.signup(
            payload,
            {"uid": "uid-1", "email": "temp@mailinator.com"},
            db,
        )


@pytest.mark.asyncio
async def test_signup_allows_disposable_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        reg_svc,
        "auth_settings",
        _auth_settings(enabled=False),
    )
    # Existing firebase user → exits after disposable check without domain rejection.
    db = Mock()
    db.execute = AsyncMock(return_value=FakeScalarResult(MagicMock()))
    db.rollback = AsyncMock()
    payload = EmailSignupRequest(
        firstName="Jane",
        lastName="Doe",
        email="temp@mailinator.com",
        password="Secret123",
        role="user",
        firebaseId="token",
    )
    with (
        patch(
            "apps.accounts.services.registration_service.delete_firebase_user_safely",
            Mock(),
        ),
        pytest.raises(ApiError, match="Account already exists"),
    ):
        await reg_svc.signup(
            payload,
            {"uid": "uid-1", "email": "temp@mailinator.com"},
            db,
        )


@pytest.mark.asyncio
async def test_admin_signup_rejects_disposable_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.auth.config.settings",
        _auth_settings(enabled=True),
    )
    payload = AdminSignupRequest(
        firstName="Admin",
        lastName="User",
        email="temp@mailinator.com",
        password="Secret123",
        role="superadmin",
    )
    result = await admin_auth.admin_signup(payload, Mock())
    assert result.status is False
    assert result.message == DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE


@pytest.mark.asyncio
async def test_admin_create_user_rejects_disposable_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.auth.config.settings",
        _auth_settings(enabled=True),
    )
    payload = AdminUserCreateRequest(
        firstName="John",
        lastName="Doe",
        email="temp@mailinator.com",
        role="moderator",
    )
    result = await admin_create_user(payload, Mock())
    assert result.status is False
    assert result.message == DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE


@pytest.mark.asyncio
async def test_social_auth_new_user_rejects_disposable_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.auth.config.settings",
        _auth_settings(enabled=True),
    )
    monkeypatch.setattr(
        "core.auth.services.verify_firebase_token",
        lambda _token, check_revoked=False: {
            "uid": f"social-{uuid4()}",
            "email": "temp@MAILINATOR.COM",
            "firebase": {"sign_in_provider": "google.com"},
        },
    )
    db = Mock()
    # No user by firebase uid, no user by email → new-user path.
    db.execute = AsyncMock(return_value=FakeScalarResult(None))
    db.flush = AsyncMock()

    payload = SocialAuthRequest(
        firebaseId="token",
        loginType="google",
        user="user",
    )
    with pytest.raises(ApiError) as exc_info:
        await reg_svc.social_auth(payload, db)
    assert str(exc_info.value.message) == DISPOSABLE_EMAIL_NOT_ALLOWED_MESSAGE
