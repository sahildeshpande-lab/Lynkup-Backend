from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.accounts.schemas import LoginRequest
from apps.accounts.services import auth_service as auth_svc
from apps.accounts.services.common_service import (
    is_soft_deleted_user,
    reactivate_soft_deleted_user,
)
from common.enums import RegistrationType, UserStatus, inactive_account_message


@pytest.fixture(autouse=True)
def _allow_public_app_login(monkeypatch):
    monkeypatch.setattr(
        "apps.accounts.services.common_service.user_has_staff_role",
        AsyncMock(return_value=False),
    )


def _deleted_user(**overrides):
    now = datetime.now(timezone.utc)
    data = {
        "id": uuid4(),
        "email": "user@example.com",
        "firebase_uid": "old-firebase-uid",
        "password_hash": auth_svc.PASSWORD_HASHER.hash("Secret123!"),
        "registration_type": RegistrationType.email,
        "status": UserStatus.deleting,
        "is_deleted": True,
        "deleted_at": now,
        "purge_after": now + timedelta(days=30),
        "last_login_at": None,
        "updated_at": now,
        "role": "user",
        "roles": [],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_reactivate_soft_deleted_user_clears_flags_and_relinks_uid():
    user = _deleted_user()
    assert is_soft_deleted_user(user) is True

    changed = reactivate_soft_deleted_user(
        user,
        firebase_uid="new-firebase-uid",
        status=UserStatus.active,
    )

    assert changed is True
    assert user.is_deleted is False
    assert user.deleted_at is None
    assert user.purge_after is None
    assert user.status == UserStatus.active
    assert user.firebase_uid == "new-firebase-uid"
    assert is_soft_deleted_user(user) is False


@pytest.mark.asyncio
async def test_login_reactivates_soft_deleted_account_within_grace_period(
    mock_db, scalar_result
):
    user = _deleted_user()
    db = mock_db(
        scalar_result(None),  # lookup by new firebase uid
        scalar_result(user),  # lookup by email
        scalar_result(user),  # reload after session
    )
    payload = LoginRequest(
        email="user@example.com",
        password="Secret123!",
        firebaseId="token",
        device_id="device-1",
    )
    firebase_user = {"uid": "new-firebase-uid", "email": "user@example.com"}

    with (
        patch.object(
            auth_svc,
            "evaluate_device_otp_requirement",
            AsyncMock(return_value=(None, False, False)),
        ),
        patch.object(auth_svc, "upsert_user_installation", AsyncMock()),
        patch.object(
            auth_svc,
            "_issue_auth_session",
            AsyncMock(return_value={"accessToken": "a", "refreshToken": "r"}),
        ),
        patch.object(auth_svc, "_fetch_user_profile", AsyncMock(return_value=None)),
        patch("apps.chat.service.sync_stream_user_on_auth", AsyncMock()),
        patch(
            "apps.user_deletion.services.account_recovery_service.run_recovery_side_effects",
            AsyncMock(),
        ) as recovery,
    ):
        result = await auth_svc.login(payload, firebase_user, db)

    assert result.status is True
    assert user.status == UserStatus.active
    assert user.is_deleted is False
    assert user.deleted_at is None
    assert user.purge_after is None
    assert user.firebase_uid == "new-firebase-uid"
    recovery.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_rejects_soft_deleted_account_after_grace_period(
    mock_db, scalar_result
):
    now = datetime.now(timezone.utc)
    user = _deleted_user(
        deleted_at=now - timedelta(days=40),
        purge_after=now - timedelta(days=1),
    )
    db = mock_db(
        scalar_result(None),  # lookup by firebase uid
        scalar_result(user),  # lookup by email
    )
    payload = LoginRequest(
        email="user@example.com",
        password="Secret123!",
        firebaseId="token",
        device_id="device-1",
    )
    firebase_user = {"uid": "new-firebase-uid", "email": "user@example.com"}

    result = await auth_svc.login(payload, firebase_user, db)

    assert result.status is False
    assert result.message == inactive_account_message(UserStatus.deleting)
    assert user.status == UserStatus.deleting
    assert user.is_deleted is True
