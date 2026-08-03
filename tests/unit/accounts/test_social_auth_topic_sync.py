from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.accounts.services import registration_service as reg_svc
from common.enums import UserStatus


def _user(*, email_verified_at=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="social@example.com",
        firebase_uid="firebase-uid",
        status=UserStatus.active,
        email_verified_at=email_verified_at,
        email_otp=None,
        email_otp_created_at=None,
        roles=[],
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_build_device_auth_session_syncs_topics_on_successful_login(mock_db):
    user = _user(email_verified_at=datetime.now(timezone.utc))
    db = mock_db()
    profile = SimpleNamespace(user_id=user.id)

    with (
        patch.object(
            reg_svc,
            "evaluate_device_otp_requirement",
            AsyncMock(return_value=(SimpleNamespace(), False, False)),
        ),
        patch.object(reg_svc, "upsert_user_installation", AsyncMock()) as upsert,
        patch.object(reg_svc, "_fetch_user_profile", AsyncMock(return_value=profile)),
        patch.object(reg_svc, "_issue_auth_session", AsyncMock(return_value={"token": "abc"})),
        patch(
            "apps.notifications.services.topic_service.TopicService.refresh_user_topic_subscriptions",
            AsyncMock(),
        ) as refresh_topics,
    ):
        session_data, message = await reg_svc._build_device_auth_session(
            db,
            user,
            "device-1",
            platform="android",
            fcm_token="fcm-token-1",
        )

    assert message == "Login successful"
    assert session_data["token"] == "abc"
    upsert.assert_awaited_once()
    assert upsert.await_args.kwargs["platform"] == "android"
    assert upsert.await_args.kwargs["fcm_token"] == "fcm-token-1"
    refresh_topics.assert_awaited_once_with(db, user.id, profile)
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_build_device_auth_session_syncs_topics_after_otp_challenge(mock_db):
    user = _user(email_verified_at=None)
    db = mock_db()
    profile = SimpleNamespace(user_id=user.id)

    with (
        patch.object(
            reg_svc,
            "evaluate_device_otp_requirement",
            AsyncMock(return_value=(None, True, True)),
        ),
        patch.object(reg_svc, "send_otp_challenge", AsyncMock(return_value="123456")),
        patch.object(reg_svc, "_fetch_user_profile", AsyncMock(return_value=profile)),
        patch.object(reg_svc, "_issue_auth_session", AsyncMock(return_value={"token": "abc"})),
        patch(
            "apps.notifications.services.topic_service.TopicService.refresh_user_topic_subscriptions",
            AsyncMock(),
        ) as refresh_topics,
    ):
        session_data, message = await reg_svc._build_device_auth_session(
            db,
            user,
            "device-1",
            platform="ios",
            fcm_token="fcm-token-2",
        )

    assert message == "Verification email sent. Please verify your OTP."
    assert session_data["needsOtp"] is True
    refresh_topics.assert_awaited_once_with(db, user.id, profile)
