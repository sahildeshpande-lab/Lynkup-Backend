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
    has_unexpired_otp,
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


def _installation(*, is_active=True, is_device_verified=True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        device_id="device-1",
        is_active=is_active,
        is_device_verified=is_device_verified,
        verified_at=datetime.now(timezone.utc) if is_device_verified else None,
        last_active_at=datetime.now(timezone.utc),
        fcm_token="fcm-token-1",
    )


@pytest.mark.asyncio
async def test_evaluate_device_otp_requirement_when_unverified_device(mock_db):
    user = _user(email_verified_at=datetime.now(timezone.utc))
    db = mock_db()

    with patch(
        "apps.accounts.services.device_otp_service.get_user_installation",
        AsyncMock(return_value=_installation(is_device_verified=False)),
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
        AsyncMock(return_value=_installation(is_active=True, is_device_verified=True)),
    ):
        _, is_new_device, needs_otp = await evaluate_device_otp_requirement(
            db,
            user,
            "device-1",
        )

    assert is_new_device is False
    assert needs_otp is False


@pytest.mark.asyncio
async def test_evaluate_device_otp_skips_otp_for_verified_device_after_logout(mock_db):
    """Verified devices skip OTP forever, even when deactivated by logout."""
    verified_at = datetime.now(timezone.utc)
    user = _user(email_verified_at=verified_at)
    inactive = _installation(is_active=False, is_device_verified=True)
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


def test_has_unexpired_otp():
    user = _user()
    assert has_unexpired_otp(user) is False

    user.email_otp = "1234"
    user.email_otp_created_at = datetime.now(timezone.utc)
    assert has_unexpired_otp(user) is True


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
        patch.object(auth_svc, "evaluate_device_otp_requirement", AsyncMock(return_value=(_installation(is_device_verified=False), False, True))),
        patch.object(auth_svc, "begin_otp_challenge", AsyncMock(return_value=True)) as begin_otp,
        patch.object(auth_svc, "_issue_auth_session", AsyncMock(return_value={"user": {"isEmailVerified": False}})),
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=None)),
    ):
        hasher.verify.return_value = True
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalar_one=lambda: user),
            ]
        )

        response = await auth_svc.login(payload, {"uid": user.firebase_uid, "email": user.email}, db)

    begin_otp.assert_awaited_once()
    assert response.status is True
    assert response.data["needsOtp"] is True
    assert response.data["emailSent"] is True
    assert response.data["isDeviceVerified"] is False


@pytest.mark.asyncio
async def test_login_reuses_unexpired_otp_without_resending(mock_db):
    user = _user(email_verified_at=datetime.now(timezone.utc))
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
        patch.object(auth_svc, "evaluate_device_otp_requirement", AsyncMock(return_value=(_installation(is_device_verified=False), False, True))),
        patch.object(auth_svc, "begin_otp_challenge", AsyncMock(return_value=False)) as begin_otp,
        patch.object(auth_svc, "_issue_auth_session", AsyncMock(return_value={"user": {"isEmailVerified": True}})),
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=None)),
    ):
        hasher.verify.return_value = True
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalar_one=lambda: user),
            ]
        )

        response = await auth_svc.login(payload, {"uid": user.firebase_uid, "email": user.email}, db)

    begin_otp.assert_awaited_once()
    assert response.status is True
    assert response.data["needsOtp"] is True
    assert response.data["emailSent"] is False
    assert response.data["isDeviceVerified"] is False
    assert response.message == "Please verify your OTP."


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
    installation = _installation(is_active=True, is_device_verified=True)
    db = mock_db()

    with (
        patch.object(auth_svc, "PASSWORD_HASHER") as hasher,
        patch.object(auth_svc, "evaluate_device_otp_requirement", AsyncMock(return_value=(installation, False, False))),
        patch.object(auth_svc, "begin_otp_challenge", AsyncMock()) as begin_otp,
        patch.object(auth_svc, "upsert_user_installation", AsyncMock(return_value=installation)),
        patch.object(auth_svc, "_issue_auth_session", AsyncMock(return_value={"user": {"isEmailVerified": True}})),
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=None)),
        patch("apps.chat.service.sync_stream_user_on_auth", AsyncMock()),
    ):
        hasher.verify.return_value = True
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalar_one=lambda: user),
            ]
        )

        response = await auth_svc.login(payload, {"uid": user.firebase_uid, "email": user.email}, db)

    begin_otp.assert_not_called()
    assert response.status is True
    assert response.message == "Login successful"
    assert response.data["needsOtp"] is False
    assert response.data["isDeviceVerified"] is True


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
        patch(
            "apps.notifications.services.topic_service.TopicService.unsubscribe_device_from_user_topics",
            AsyncMock(),
        ) as unsubscribe,
    ):
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: user),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [installation])),
                SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])),
            ]
        )
        payload = SimpleNamespace(device_id="device-1")

        await session_svc.logout(payload, {"uid": user.firebase_uid}, db)

    assert user.email_verified_at == verified_at
    assert user.email_otp is None
    assert user.email_otp_created_at is None
    assert installation.is_active is False
    assert installation.fcm_token is None
    unsubscribe.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_begin_otp_challenge_reuses_unexpired_otp(mock_db):
    from apps.accounts.services.device_otp_service import begin_otp_challenge

    user = _user(email_verified_at=datetime.now(timezone.utc))
    user.email_otp = "4242"
    user.email_otp_created_at = datetime.now(timezone.utc)
    installation = _installation(is_device_verified=False)
    db = mock_db()

    with (
        patch(
            "apps.accounts.services.device_otp_service.ensure_unverified_installation",
            AsyncMock(return_value=installation),
        ),
        patch(
            "apps.accounts.services.device_otp_service.send_otp_email",
            AsyncMock(),
        ) as send_email,
        patch(
            "apps.accounts.services.device_otp_service._generate_otp",
            return_value="9999",
        ),
    ):
        email_sent = await begin_otp_challenge(
            db,
            user,
            "device-1",
            installation=installation,
            is_new_device=False,
        )

    assert email_sent is False
    assert user.email_otp == "4242"
    send_email.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_begin_otp_challenge_sends_new_otp_when_missing(mock_db):
    from apps.accounts.services.device_otp_service import begin_otp_challenge

    user = _user(email_verified_at=datetime.now(timezone.utc))
    installation = _installation(is_device_verified=False)
    db = mock_db()

    with (
        patch(
            "apps.accounts.services.device_otp_service.ensure_unverified_installation",
            AsyncMock(return_value=installation),
        ),
        patch(
            "apps.accounts.services.device_otp_service.send_otp_email",
            AsyncMock(),
        ) as send_email,
        patch(
            "apps.accounts.services.device_otp_service._generate_otp",
            return_value="5678",
        ),
    ):
        email_sent = await begin_otp_challenge(
            db,
            user,
            "device-1",
            installation=None,
            is_new_device=True,
        )

    assert email_sent is True
    assert user.email_otp == "5678"
    assert user.email_otp_created_at is not None
    send_email.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_device_verified_updates_installation(mock_db):
    from apps.accounts.services.device_otp_service import mark_device_verified

    installation = _installation(is_active=True, is_device_verified=False)
    installation.verified_at = None
    db = mock_db()

    with patch(
        "apps.accounts.services.device_otp_service.get_user_installation",
        AsyncMock(return_value=installation),
    ):
        result = await mark_device_verified(db, uuid.uuid4(), "device-1")

    assert result is installation
    assert installation.is_device_verified is True
    assert installation.verified_at is not None
    assert installation.is_active is True
    db.add.assert_called()


@pytest.mark.asyncio
async def test_mark_pending_active_device_verified_uses_active_install(mock_db):
    from apps.accounts.services.device_otp_service import mark_pending_active_device_verified

    installation = _installation(is_active=True, is_device_verified=False)
    installation.verified_at = None
    db = mock_db()
    user_id = uuid.uuid4()

    with (
        patch(
            "apps.accounts.services.device_otp_service.get_pending_active_unverified_installation",
            AsyncMock(return_value=installation),
        ),
        patch(
            "apps.accounts.services.device_otp_service.mark_device_verified",
            AsyncMock(return_value=installation),
        ) as mark_verified,
    ):
        result = await mark_pending_active_device_verified(db, user_id)

    assert result is installation
    mark_verified.assert_awaited_once_with(db, user_id, "device-1", now=None)


@pytest.mark.asyncio
async def test_mark_pending_active_device_verified_none_when_missing(mock_db):
    from apps.accounts.services.device_otp_service import mark_pending_active_device_verified

    db = mock_db()

    with patch(
        "apps.accounts.services.device_otp_service.get_pending_active_unverified_installation",
        AsyncMock(return_value=None),
    ):
        result = await mark_pending_active_device_verified(db, uuid.uuid4())

    assert result is None


@pytest.mark.asyncio
async def test_verify_otp_marks_device_and_clears_otp(mock_db):
    user = _user()
    user.onboarding_status = "completed"
    user.email_otp = "1234"
    user.email_otp_created_at = datetime.now(timezone.utc)
    payload = SimpleNamespace(
        email=user.email,
        otp="1234",
    )
    pending = _installation(is_active=True, is_device_verified=False)
    db = mock_db()

    with (
        patch.object(
            auth_svc,
            "mark_pending_active_device_verified",
            AsyncMock(return_value=pending),
        ) as mark_pending,
        patch.object(auth_svc, "_issue_auth_session", AsyncMock(return_value={"user": {}})),
        patch.object(auth_svc, "send_verification_success_email", AsyncMock()) as success_email,
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
    assert user.email_otp is None
    assert user.email_otp_created_at is None
    mark_pending.assert_awaited_once()
    success_email.assert_not_called()
    assert response.data["needsOtp"] is False
    assert response.data["emailSent"] is False
    assert response.data["isDeviceVerified"] is True


@pytest.mark.asyncio
async def test_resend_otp_replaces_existing_code(mock_db):
    user = _user()
    user.email_otp = "1111"
    user.email_otp_created_at = datetime.now(timezone.utc)
    payload = SimpleNamespace(email=user.email, device_id="device-1", platform=None)
    db = mock_db()

    with (
        patch.object(auth_svc, "_generate_otp", return_value="2222"),
        patch.object(auth_svc, "send_otp_email", AsyncMock()) as send_email,
        patch.object(auth_svc, "ensure_unverified_installation", AsyncMock()) as ensure_inst,
    ):
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))

        response = await auth_svc.resend_otp(
            payload,
            {"uid": user.firebase_uid},
            db,
        )

    assert response.status is True
    assert user.email_otp == "2222"
    assert user.email_otp_created_at is not None
    ensure_inst.assert_awaited_once()
    send_email.assert_awaited_once()
    db.commit.assert_awaited_once()
