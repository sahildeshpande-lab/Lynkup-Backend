from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.accounts.services import auth_service as auth_svc
from apps.accounts.services.common_service import (
    _generate_otp,
    can_reuse_stored_otp,
    is_static_otp_account,
    otp_matches,
)
from apps.accounts.services.device_otp_service import begin_otp_challenge
from common.enums import UserStatus
from core.auth.config import AuthSettings

STATIC_OTP_EMAIL = "avinash.patil@yopmail.com"
STATIC_OTP_CODE = "1234"


@pytest.fixture
def static_otp_settings(monkeypatch):
    from core.auth.config import settings as auth_settings

    monkeypatch.setattr(auth_settings, "static_otp_email", STATIC_OTP_EMAIL)
    monkeypatch.setattr(auth_settings, "static_otp_code", STATIC_OTP_CODE)
    return auth_settings


def _user(*, email=STATIC_OTP_EMAIL, email_otp=None, email_otp_created_at=None):
    return SimpleNamespace(
        id=uuid.UUID("6903409c-0de2-4769-9f48-d9ed5c2ab1a5"),
        email=email,
        firebase_uid="firebase-uid",
        password_hash="hashed",
        registration_type="email",
        status=UserStatus.pending,
        deleted_at=None,
        email_verified_at=None,
        email_otp=email_otp,
        email_otp_created_at=email_otp_created_at,
        onboarding_status="completed",
        roles=[],
        has_changed_email_after_graduation=False,
        updated_at=datetime.now(timezone.utc),
    )


def test_static_otp_defaults_to_test_account():
    assert AuthSettings.model_fields["static_otp_email"].default == STATIC_OTP_EMAIL
    assert AuthSettings.model_fields["static_otp_code"].default == STATIC_OTP_CODE
    assert "static_otp_enabled" not in AuthSettings.model_fields


def test_generate_otp_uses_static_code_for_test_account(static_otp_settings):
    assert _generate_otp(STATIC_OTP_EMAIL) == STATIC_OTP_CODE
    assert _generate_otp("Avinash.Patil@yopmail.com") == STATIC_OTP_CODE


def test_generate_otp_is_random_for_normal_users(static_otp_settings, monkeypatch):
    monkeypatch.setattr(
        "apps.accounts.services.common_service.secrets.randbelow",
        lambda _n: 4678,
    )
    assert is_static_otp_account("normal.user@example.com") is False
    assert _generate_otp("normal.user@example.com") == "5678"
    assert _generate_otp(STATIC_OTP_EMAIL) == STATIC_OTP_CODE


def test_otp_matches_accepts_only_static_code(static_otp_settings):
    user = _user(email_otp=STATIC_OTP_CODE, email_otp_created_at=datetime.now(timezone.utc))
    assert otp_matches(user, STATIC_OTP_CODE) is True
    assert otp_matches(user, "9999") is False
    assert otp_matches(user, "5678") is False


def test_otp_matches_rejects_other_stored_values_for_test_account(static_otp_settings):
    user = _user(email_otp="5678", email_otp_created_at=datetime.now(timezone.utc))
    assert otp_matches(user, "5678") is False
    assert otp_matches(user, STATIC_OTP_CODE) is True


def test_otp_matches_requires_issued_otp(static_otp_settings):
    user = _user(email_otp=None)
    assert otp_matches(user, STATIC_OTP_CODE) is False


def test_can_reuse_stored_otp_rejects_leftover_random_code(static_otp_settings):
    leftover = _user(email_otp="5678")
    current = _user(email_otp=STATIC_OTP_CODE)
    normal = _user(email="other@example.com", email_otp="5678")
    assert can_reuse_stored_otp(leftover) is False
    assert can_reuse_stored_otp(current) is True
    assert can_reuse_stored_otp(normal) is True


@pytest.mark.asyncio
async def test_begin_otp_challenge_stores_static_otp(static_otp_settings, mock_db):
    user = _user()
    db = mock_db()

    with (
        patch(
            "apps.accounts.services.device_otp_service.ensure_unverified_installation",
            AsyncMock(),
        ),
        patch(
            "apps.accounts.services.device_otp_service.send_otp_email",
            AsyncMock(),
        ) as send_email,
        patch(
            "apps.accounts.services.device_otp_service._fetch_user_profile",
            AsyncMock(return_value=None),
        ),
    ):
        email_sent = await begin_otp_challenge(db, user, "device-1")

    assert email_sent is True
    assert user.email_otp == STATIC_OTP_CODE
    assert user.email_otp_created_at is not None
    send_email.assert_awaited_once()
    assert send_email.await_args.args[1] == STATIC_OTP_CODE


@pytest.mark.asyncio
async def test_begin_otp_challenge_replaces_leftover_non_static_otp(static_otp_settings, mock_db):
    user = _user(
        email_otp="5678",
        email_otp_created_at=datetime.now(timezone.utc),
    )
    db = mock_db()

    with (
        patch(
            "apps.accounts.services.device_otp_service.ensure_unverified_installation",
            AsyncMock(),
        ),
        patch(
            "apps.accounts.services.device_otp_service.send_otp_email",
            AsyncMock(),
        ),
        patch(
            "apps.accounts.services.device_otp_service._fetch_user_profile",
            AsyncMock(return_value=None),
        ),
    ):
        email_sent = await begin_otp_challenge(db, user, "device-1")

    assert email_sent is True
    assert user.email_otp == STATIC_OTP_CODE


@pytest.mark.asyncio
async def test_begin_otp_challenge_is_random_for_normal_users(static_otp_settings, mock_db, monkeypatch):
    user = _user(email="normal.user@example.com")
    db = mock_db()
    monkeypatch.setattr(
        "apps.accounts.services.common_service.secrets.randbelow",
        lambda _n: 4678,
    )

    with (
        patch(
            "apps.accounts.services.device_otp_service.ensure_unverified_installation",
            AsyncMock(),
        ),
        patch(
            "apps.accounts.services.device_otp_service.send_otp_email",
            AsyncMock(),
        ) as send_email,
        patch(
            "apps.accounts.services.device_otp_service._fetch_user_profile",
            AsyncMock(return_value=None),
        ),
    ):
        email_sent = await begin_otp_challenge(db, user, "device-1")

    assert email_sent is True
    assert user.email_otp == "5678"
    assert send_email.await_args.args[1] == "5678"


@pytest.mark.asyncio
async def test_resend_otp_stores_static_code(static_otp_settings, mock_db):
    user = _user(email_otp="1111", email_otp_created_at=datetime.now(timezone.utc))
    payload = SimpleNamespace(email=user.email, device_id="device-1", platform=None)
    db = mock_db()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))

    with (
        patch.object(auth_svc, "send_otp_email", AsyncMock()) as send_email,
        patch.object(auth_svc, "ensure_unverified_installation", AsyncMock()),
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=None)),
    ):
        response = await auth_svc.resend_otp(payload, {"uid": user.firebase_uid}, db)

    assert response.status is True
    assert user.email_otp == STATIC_OTP_CODE
    assert user.email_otp_created_at is not None
    send_email.assert_awaited_once()
    assert send_email.await_args.args[1] == STATIC_OTP_CODE


@pytest.mark.asyncio
async def test_resend_otp_is_random_for_normal_users(static_otp_settings, mock_db, monkeypatch):
    user = _user(email="normal.user@example.com", email_otp="1111")
    payload = SimpleNamespace(email=user.email, device_id="device-1", platform=None)
    db = mock_db()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))
    monkeypatch.setattr(
        "apps.accounts.services.common_service.secrets.randbelow",
        lambda _n: 2222 - 1000,
    )

    with (
        patch.object(auth_svc, "send_otp_email", AsyncMock()),
        patch.object(auth_svc, "ensure_unverified_installation", AsyncMock()),
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=None)),
    ):
        response = await auth_svc.resend_otp(payload, {"uid": user.firebase_uid}, db)

    assert response.status is True
    assert user.email_otp == "2222"


@pytest.mark.asyncio
async def test_verify_otp_accepts_static_code_and_clears_it(static_otp_settings, mock_db):
    user = _user(
        email_otp=STATIC_OTP_CODE,
        email_otp_created_at=datetime.now(timezone.utc),
    )
    payload = SimpleNamespace(email=user.email, otp=STATIC_OTP_CODE)
    db = mock_db()
    pending = SimpleNamespace(device_id="device-1")

    with (
        patch.object(
            auth_svc,
            "mark_pending_active_device_verified",
            AsyncMock(return_value=pending),
        ),
        patch.object(auth_svc, "_issue_auth_session", AsyncMock(return_value={"user": {}})),
        patch.object(auth_svc, "send_verification_success_email", AsyncMock()),
        patch("apps.chat.service.sync_stream_user_on_auth", AsyncMock()),
    ):
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalar_one_or_none=lambda: None),
                SimpleNamespace(scalar_one=lambda: user),
            ]
        )
        response = await auth_svc.verify_otp(
            payload,
            {"uid": user.firebase_uid, "email": user.email},
            db,
        )

    assert response.status is True
    assert response.message == "OTP successfully verified."
    assert user.email_otp is None
    assert user.email_otp_created_at is None


@pytest.mark.asyncio
async def test_verify_otp_rejects_incorrect_code_for_test_account(static_otp_settings, mock_db):
    user = _user(
        email_otp=STATIC_OTP_CODE,
        email_otp_created_at=datetime.now(timezone.utc),
    )
    payload = SimpleNamespace(email=user.email, otp="9999")
    db = mock_db()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))

    response = await auth_svc.verify_otp(
        payload,
        {"uid": user.firebase_uid, "email": user.email},
        db,
    )

    assert response.status is False
    assert response.message == "Incorrect OTP. Please try again."
    assert user.email_otp == STATIC_OTP_CODE


@pytest.mark.asyncio
async def test_resend_after_successful_verify_sets_static_otp_again(static_otp_settings, mock_db):
    user = _user(
        email_otp=STATIC_OTP_CODE,
        email_otp_created_at=datetime.now(timezone.utc),
    )
    db = mock_db()
    pending = SimpleNamespace(device_id="device-1")

    with (
        patch.object(
            auth_svc,
            "mark_pending_active_device_verified",
            AsyncMock(return_value=pending),
        ),
        patch.object(auth_svc, "_issue_auth_session", AsyncMock(return_value={"user": {}})),
        patch.object(auth_svc, "send_verification_success_email", AsyncMock()),
        patch("apps.chat.service.sync_stream_user_on_auth", AsyncMock()),
    ):
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalar_one_or_none=lambda: None),
                SimpleNamespace(scalar_one=lambda: user),
            ]
        )
        verify_response = await auth_svc.verify_otp(
            SimpleNamespace(email=user.email, otp=STATIC_OTP_CODE),
            {"uid": user.firebase_uid, "email": user.email},
            db,
        )

    assert verify_response.status is True
    assert user.email_otp is None

    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))
    with (
        patch.object(auth_svc, "send_otp_email", AsyncMock()) as send_email,
        patch.object(auth_svc, "ensure_unverified_installation", AsyncMock()),
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=None)),
    ):
        resend_response = await auth_svc.resend_otp(
            SimpleNamespace(email=user.email, device_id="device-1", platform=None),
            {"uid": user.firebase_uid},
            db,
        )

    assert resend_response.status is True
    assert user.email_otp == STATIC_OTP_CODE
    assert user.email_otp_created_at is not None
    assert send_email.await_args.args[1] == STATIC_OTP_CODE
