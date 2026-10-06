from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.accounts.schemas import ForgotPasswordRequest
from common.enums import UserStatus


def _active_user(*, spec=None):
    user = MagicMock(spec=spec) if spec is not None else MagicMock()
    user.id = uuid4()
    user.email = "user@example.com"
    user.status = UserStatus.active
    user.is_deleted = False
    user.deleted_at = None
    return user


@pytest.mark.asyncio
async def test_forgot_password_uses_firebase_native_email(monkeypatch, db_scalar_result):
    from apps.accounts.services import forgot_password

    user = _active_user()

    mock_db = AsyncMock()
    existing_token_result = db_scalar_result(None)
    mock_db.execute = AsyncMock(side_effect=[db_scalar_result(user), existing_token_result])
    send_calls: list[str] = []

    async def mock_send_firebase_password_reset_email(email: str, reset_link: str) -> bool:
        send_calls.append(email)
        assert reset_link.startswith("http://") or reset_link.startswith("https://")
        return True

    monkeypatch.setattr(
        "apps.accounts.services.password_service.send_reset_password_email",
        mock_send_firebase_password_reset_email,
    )

    payload = ForgotPasswordRequest(email="USER@example.com", firebaseId="firebase-token")
    result = await forgot_password(payload, mock_db)

    assert result.status is True
    assert result.message == "Password reset link sent successfully to your mail"
    assert result.data is None
    assert send_calls == ["user@example.com"]
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_firebase_password_reset_failure_returns_error_response(monkeypatch, db_scalar_result):
    from apps.accounts import services
    from apps.accounts.db_models import User

    user = _active_user(spec=User)

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=[db_scalar_result(user), db_scalar_result(None)])

    async def mock_send_reset_password_email(*args, **kwargs):
        raise RuntimeError("Failed to send password reset email")

    monkeypatch.setattr(
        "apps.accounts.services.password_service.send_reset_password_email",
        mock_send_reset_password_email,
    )

    result = await services.forgot_password(ForgotPasswordRequest(email="user@example.com"), mock_db)

    assert result.status is False
    assert result.message == "Failed to send password reset email. Please try again."
    mock_db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_password_revokes_firebase_and_device_tokens(monkeypatch, db_scalar_result):
    from apps.accounts.schemas import UserChangePasswordRequest
    from apps.accounts.services.password_service import change_password, PASSWORD_HASHER
    from apps.accounts.db_models import User, RefreshToken

    user = _active_user(spec=User)
    user.firebase_uid = "firebase-uid-abc"
    user.password_hash = PASSWORD_HASHER.hash("OldPassword123!")

    refresh_token = RefreshToken(user_id=user.id, token_hash="hash123")

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            # User lookup
            db_scalar_result(user),
            # RefreshToken lookup
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[refresh_token])))),
        ]
    )

    revoked_firebase_uids: list[str] = []
    updated_passwords: list[tuple[str, str]] = []
    revoked_stream_users: list[User] = []
    stored_refresh: list[str] = []

    monkeypatch.setattr(
        "apps.accounts.services.password_service.verify_firebase_token",
        lambda token, check_revoked=False: {"uid": "firebase-uid-abc"},
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service.update_firebase_password",
        lambda uid, password: updated_passwords.append((uid, password)),
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service.revoke_firebase_tokens",
        lambda uid: revoked_firebase_uids.append(uid),
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service.create_firebase_custom_token",
        lambda uid: f"custom-token-for-{uid}",
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service._generate_tokens",
        lambda _user: ("new-access", "new-refresh"),
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service._store_refresh_token",
        AsyncMock(side_effect=lambda db, u, token, device_id=None: stored_refresh.append(token)),
    )
    monkeypatch.setattr(
        "apps.chat.service.revoke_stream_user_tokens_best_effort",
        AsyncMock(side_effect=lambda u: revoked_stream_users.append(u)),
    )

    payload = UserChangePasswordRequest(
        firebaseId="valid-firebase-token",
        current_password="OldPassword123!",
        new_password="NewPassword456!",
    )

    result = await change_password(payload, mock_db)

    assert result.status is True
    assert result.message == "Password updated successfully"
    assert updated_passwords == [("firebase-uid-abc", "NewPassword456!")]
    # Verify Firebase tokens were revoked for the user
    assert revoked_firebase_uids == ["firebase-uid-abc"]
    # Verify backend DB refresh tokens were marked revoked
    assert refresh_token.revoked_at is not None
    # Verify Stream chat tokens were revoked
    assert revoked_stream_users == [user]
    # Calling device gets a fresh session (no device_id required from FE)
    assert stored_refresh == ["new-refresh"]
    assert result.data == {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "token_type": "bearer",
        "firebaseCustomToken": "custom-token-for-firebase-uid-abc",
    }
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_password_rejects_incorrect_current_password(monkeypatch, db_scalar_result):
    from apps.accounts.schemas import UserChangePasswordRequest
    from apps.accounts.services.password_service import change_password, PASSWORD_HASHER
    from apps.accounts.db_models import User

    original_hash = PASSWORD_HASHER.hash("OldPassword123!")
    user = _active_user(spec=User)
    user.firebase_uid = "firebase-uid-abc"
    user.password_hash = original_hash

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalar_result(user))

    updated_passwords: list[tuple[str, str]] = []
    revoked_firebase_uids: list[str] = []
    revoked_stream_users: list[User] = []
    stored_refresh: list[str] = []

    monkeypatch.setattr(
        "apps.accounts.services.password_service.verify_firebase_token",
        lambda token, check_revoked=False: {"uid": "firebase-uid-abc"},
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service.update_firebase_password",
        lambda uid, password: updated_passwords.append((uid, password)),
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service.revoke_firebase_tokens",
        lambda uid: revoked_firebase_uids.append(uid),
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service._generate_tokens",
        lambda _user: ("new-access", "new-refresh"),
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service._store_refresh_token",
        AsyncMock(side_effect=lambda db, u, token, device_id=None: stored_refresh.append(token)),
    )
    monkeypatch.setattr(
        "apps.chat.service.revoke_stream_user_tokens_best_effort",
        AsyncMock(side_effect=lambda u: revoked_stream_users.append(u)),
    )

    payload = UserChangePasswordRequest(
        firebaseId="valid-firebase-token",
        current_password="WrongPassword123!",
        new_password="NewPassword456!",
    )

    result = await change_password(payload, mock_db)

    assert result.status is False
    assert result.message == "Existing password does not match"
    assert result.data is None
    assert user.password_hash == original_hash
    assert updated_passwords == []
    assert revoked_firebase_uids == []
    assert revoked_stream_users == []
    assert stored_refresh == []
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_password_rejects_same_new_password(monkeypatch, db_scalar_result):
    from apps.accounts.schemas import UserChangePasswordRequest
    from apps.accounts.services.password_service import change_password, PASSWORD_HASHER
    from apps.accounts.db_models import User, RefreshToken

    original_hash = PASSWORD_HASHER.hash("Password123")
    user = _active_user(spec=User)
    user.firebase_uid = "firebase-uid-abc"
    user.password_hash = original_hash

    refresh_token = RefreshToken(user_id=user.id, token_hash="hash123")

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalar_result(user))

    updated_passwords: list[tuple[str, str]] = []
    revoked_firebase_uids: list[str] = []
    revoked_stream_users: list[User] = []
    stored_refresh: list[str] = []

    monkeypatch.setattr(
        "apps.accounts.services.password_service.verify_firebase_token",
        lambda token, check_revoked=False: {"uid": "firebase-uid-abc"},
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service.update_firebase_password",
        lambda uid, password: updated_passwords.append((uid, password)),
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service.revoke_firebase_tokens",
        lambda uid: revoked_firebase_uids.append(uid),
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service.create_firebase_custom_token",
        lambda uid: f"custom-token-for-{uid}",
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service._generate_tokens",
        lambda _user: ("new-access", "new-refresh"),
    )
    monkeypatch.setattr(
        "apps.accounts.services.password_service._store_refresh_token",
        AsyncMock(side_effect=lambda db, u, token, device_id=None: stored_refresh.append(token)),
    )
    monkeypatch.setattr(
        "apps.chat.service.revoke_stream_user_tokens_best_effort",
        AsyncMock(side_effect=lambda u: revoked_stream_users.append(u)),
    )

    payload = UserChangePasswordRequest(
        firebaseId="valid-firebase-token",
        current_password="Password123",
        new_password="Password123",
    )

    result = await change_password(payload, mock_db)

    assert result.status is False
    assert result.message == "New password cannot be the same as current password"
    assert result.data is None
    assert user.password_hash == original_hash
    assert updated_passwords == []
    assert revoked_firebase_uids == []
    assert refresh_token.revoked_at is None
    assert revoked_stream_users == []
    assert stored_refresh == []
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_current_user_unauthorized_on_revoked_token(monkeypatch):
    from core.security.auth import get_current_user
    from fastapi.security import HTTPAuthorizationCredentials
    from common.exceptions import ApiError

    mock_db = AsyncMock()

    def _mock_verify(token, check_revoked=True):
        assert check_revoked is True
        raise RuntimeError("Token has been revoked")

    monkeypatch.setattr("core.auth.services.verify_firebase_token", _mock_verify)

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="revoked-token")

    with pytest.raises(ApiError) as exc_info:
        await get_current_user(creds, mock_db)

    assert exc_info.value.message == "Invalid access token"
    from entrypoints.api import _api_error_status_code
    assert _api_error_status_code(exc_info.value.message) == 401

