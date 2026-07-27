from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.accounts.services import auth_service as auth_svc
from apps.accounts.services import session_service as session_svc
from apps.accounts.services.device_otp_service import (
    clear_session_email_verification,
    evaluate_device_otp_requirement,
)
from common.enums import UserStatus


def _user(*, email_verified_at=None, password_hash="hashed", email="user@example.com", registration_type="email"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=email,
        firebase_uid="firebase-uid",
        password_hash=password_hash,
        registration_type=registration_type,
        status=UserStatus.active,
        deleted_at=None,
        email_verified_at=email_verified_at,
        email_otp=None,
        email_otp_created_at=None,
        roles=[],
    )


def _installation():
    return SimpleNamespace(
        id=uuid.uuid4(),
        device_id="device-1",
        is_active=True,
        last_active_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_evaluate_device_otp_requirement_when_unverified(mock_db):
    user = _user(email_verified_at=None)
    db = mock_db()

    with patch(
        "apps.accounts.services.device_otp_service.get_user_installation",
        AsyncMock(return_value=_installation()),
    ):
        installation, is_new_device, needs_otp = await evaluate_device_otp_requirement(
            db,
            user,
            "device-1",
        )

    assert installation is not None
    assert is_new_device is False
    assert needs_otp is True


@pytest.mark.asyncio
async def test_evaluate_device_otp_requirement_for_new_device(mock_db):
    verified_at = datetime.now(timezone.utc)
    user = _user(email_verified_at=verified_at)
    db = mock_db()

    with patch(
        "apps.accounts.services.device_otp_service.get_user_installation",
        AsyncMock(return_value=None),
    ):
        installation, is_new_device, needs_otp = await evaluate_device_otp_requirement(
            db,
            user,
            "device-2",
        )

    assert installation is None
    assert is_new_device is True
    assert needs_otp is True


@pytest.mark.asyncio
async def test_evaluate_device_otp_requirement_when_verified_same_device(mock_db):
    verified_at = datetime.now(timezone.utc)
    user = _user(email_verified_at=verified_at)
    db = mock_db()

    with patch(
        "apps.accounts.services.device_otp_service.get_user_installation",
        AsyncMock(return_value=_installation()),
    ):
        _, is_new_device, needs_otp = await evaluate_device_otp_requirement(
            db,
            user,
            "device-1",
        )

    assert is_new_device is False
    assert needs_otp is False


@pytest.mark.asyncio
async def test_evaluate_device_otp_skips_known_inactive_device_after_logout(mock_db):
    """Deactivated installation (manual logout) is still a known/trusted device."""
    verified_at = datetime.now(timezone.utc)
    user = _user(email_verified_at=verified_at)
    inactive = _installation()
    inactive.is_active = False
    db = mock_db()

    with patch(
        "apps.accounts.services.device_otp_service.get_user_installation",
        AsyncMock(return_value=inactive),
    ):
        installation, is_new_device, needs_otp = await evaluate_device_otp_requirement(
            db,
            user,
            "device-1",
        )

    assert installation is inactive
    assert is_new_device is False
    assert needs_otp is False


def test_clear_session_email_verification():
    user = _user(email_verified_at=datetime.now(timezone.utc))
    user.email_otp = "1234"
    user.email_otp_created_at = datetime.now(timezone.utc)

    clear_session_email_verification(user)

    assert user.email_verified_at is None
    assert user.email_otp is None
    assert user.email_otp_created_at is None


@pytest.mark.asyncio
async def test_login_sends_otp_when_verification_required(mock_db):
    user = _user(email_verified_at=None)
    payload = SimpleNamespace(
        email=user.email,
        device_id="device-1",
        password="Secret123",
        platform=None,
        fcm_token=None,
    )
    db = mock_db()

    with (
        patch.object(auth_svc, "PASSWORD_HASHER") as hasher,
        patch.object(auth_svc, "evaluate_device_otp_requirement", AsyncMock(return_value=(_installation(), False, True))),
        patch.object(auth_svc, "send_otp_challenge", AsyncMock()) as send_otp,
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=SimpleNamespace(user_id=user.id))),
        patch(
            "apps.notifications.services.topic_service.TopicService.build_topics",
            AsyncMock(return_value=set()),
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.sync_topics",
            AsyncMock(),
        ) as sync_topics,
        patch.object(auth_svc, "_issue_auth_session", AsyncMock(return_value={"user": {"isEmailVerified": False}})),
    ):
        hasher.verify.return_value = True
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalar_one=lambda: user),
            ]
        )

        response = await auth_svc.login(payload, {"uid": user.firebase_uid, "email": user.email}, db)

    send_otp.assert_awaited_once()
    sync_topics.assert_awaited_once()
    assert response.status is True
    assert response.data["needsOtp"] is True
    assert response.data["emailSent"] is True


@pytest.mark.asyncio
async def test_login_success_without_otp_when_verified_same_device(mock_db):
    verified_at = datetime.now(timezone.utc)
    user = _user(email_verified_at=verified_at)
    payload = SimpleNamespace(
        email=user.email,
        device_id="device-1",
        password="Secret123",
        platform=None,
        fcm_token=None,
    )
    installation = _installation()
    db = mock_db()

    with (
        patch.object(auth_svc, "PASSWORD_HASHER") as hasher,
        patch.object(auth_svc, "evaluate_device_otp_requirement", AsyncMock(return_value=(installation, False, False))),
        patch.object(auth_svc, "send_otp_challenge", AsyncMock()) as send_otp,
        patch.object(auth_svc, "upsert_user_installation", AsyncMock()),
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=SimpleNamespace(user_id=user.id))),
        patch(
            "apps.notifications.services.topic_service.TopicService.build_topics",
            AsyncMock(return_value=set()),
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.sync_topics",
            AsyncMock(),
        ) as sync_topics,
        patch.object(auth_svc, "_issue_auth_session", AsyncMock(return_value={"user": {"isEmailVerified": True}})),
    ):
        hasher.verify.return_value = True
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalar_one=lambda: user),
            ]
        )

        response = await auth_svc.login(payload, {"uid": user.firebase_uid, "email": user.email}, db)

    send_otp.assert_not_called()
    sync_topics.assert_awaited_once()
    assert response.status is True
    assert response.message == "Login successful"
    assert response.data["needsOtp"] is False


@pytest.mark.asyncio
async def test_login_topic_sync_failure_does_not_break_login(mock_db):
    verified_at = datetime.now(timezone.utc)
    user = _user(email_verified_at=verified_at)
    payload = SimpleNamespace(
        email=user.email,
        device_id="device-1",
        password="Secret123",
        platform=None,
        fcm_token=None,
    )
    installation = _installation()
    db = mock_db()

    with (
        patch.object(auth_svc, "PASSWORD_HASHER") as hasher,
        patch.object(auth_svc, "evaluate_device_otp_requirement", AsyncMock(return_value=(installation, False, False))),
        patch.object(auth_svc, "send_otp_challenge", AsyncMock()),
        patch.object(auth_svc, "upsert_user_installation", AsyncMock()),
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=SimpleNamespace(user_id=user.id))),
        patch(
            "apps.notifications.services.topic_service.TopicService.build_topics",
            AsyncMock(return_value=set()),
        ),
        patch(
            "apps.notifications.services.topic_service.TopicService.sync_topics",
            AsyncMock(side_effect=Exception("firebase down")),
        ),
        patch.object(auth_svc, "_issue_auth_session", AsyncMock(return_value={"user": {"isEmailVerified": True}})),
    ):
        hasher.verify.return_value = True
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalar_one=lambda: user),
            ]
        )

        response = await auth_svc.login(payload, {"uid": user.firebase_uid, "email": user.email}, db)

    assert response.status is True
    assert response.message == "Login successful"


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(mock_db):
    user = _user(email_verified_at=datetime.now(timezone.utc))
    payload = SimpleNamespace(email=user.email, device_id="device-1", password="WrongPass1")
    db = mock_db()

    with patch.object(auth_svc, "PASSWORD_HASHER") as hasher:
        hasher.verify.return_value = False
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))

        response = await auth_svc.login(payload, {"uid": user.firebase_uid, "email": user.email}, db)

    assert response.status is False
    assert response.message == "Invalid credentials"


@pytest.mark.asyncio
async def test_login_rejects_social_account_without_password_hash(mock_db):
    user = _user(password_hash=None, registration_type="google")
    payload = SimpleNamespace(email=user.email, device_id="device-1", password="Secret123")
    db = mock_db()

    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))

    response = await auth_svc.login(payload, {"uid": user.firebase_uid, "email": user.email}, db)

    assert response.status is False
    assert response.message == (
        "This account uses Google Sign-In. Use Google or reset your password."
    )


@pytest.mark.asyncio
async def test_login_rejects_apple_account_without_password_hash(mock_db):
    user = _user(password_hash=None, registration_type="apple")
    payload = SimpleNamespace(email=user.email, device_id="device-1", password="Secret123")
    db = mock_db()

    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))

    response = await auth_svc.login(payload, {"uid": user.firebase_uid, "email": user.email}, db)

    assert response.status is False
    assert response.message == (
        "This account uses Apple Sign-In. Use Apple or reset your password."
    )

@pytest.mark.asyncio
async def test_logout_preserves_email_verification_for_known_device(mock_db):
    verified_at = datetime.now(timezone.utc)
    user = _user(email_verified_at=verified_at)
    user.email_otp = "1234"
    user.email_otp_created_at = datetime.now(timezone.utc)
    installation = _installation()
    db = mock_db()

    with (
        patch.object(session_svc, "_revoke_refresh_token_row", AsyncMock()),
        patch("apps.accounts.services.session_service.revoke_firebase_tokens"),
    ):
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalar_one_or_none=lambda: installation),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
            ]
        )
        payload = SimpleNamespace(device_id="device-1")

        await session_svc.logout(payload, {"uid": user.firebase_uid}, db)

    assert user.email_verified_at == verified_at
    assert user.email_otp is None
    assert user.email_otp_created_at is None
    assert installation.is_active is False
    db.commit.assert_awaited_once()
