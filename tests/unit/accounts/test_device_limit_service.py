from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest
from common.exceptions import ApiError
from apps.accounts.services.device_limit_service import validate_device_account_limit
from apps.accounts.schemas import EmailSignupRequest, LoginRequest
from apps.accounts.services import signup, login


@pytest.mark.asyncio
async def test_validate_device_account_limit_passes_when_below_limit():
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = 1
    db.execute = AsyncMock(return_value=mock_result)

    # Should not raise
    await validate_device_account_limit(db, "device-123")
    assert db.execute.called


@pytest.mark.asyncio
async def test_validate_device_account_limit_ignores_empty_device():
    db = AsyncMock()
    await validate_device_account_limit(db, None)
    await validate_device_account_limit(db, "   ")
    assert not db.execute.called


@pytest.mark.asyncio
async def test_validate_device_account_limit_raises_when_exceeded():
    from core.auth.config import settings as auth_settings

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = 5
    db.execute = AsyncMock(return_value=mock_result)

    with patch.object(auth_settings, "max_accounts_per_device", 3):
        with pytest.raises(ApiError) as exc:
            await validate_device_account_limit(db, "device-123")
    assert "The maximum number of accounts allowed on this device has been reached." in str(exc.value)


@pytest.mark.asyncio
async def test_validate_device_account_limit_with_user_id_passes():
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = 2
    db.execute = AsyncMock(return_value=mock_result)

    user_id = uuid4()
    await validate_device_account_limit(db, "device-123", user_id=user_id)
    assert db.execute.called


@pytest.mark.asyncio
async def test_signup_returns_account_limit_exceeded():
    db = AsyncMock()
    payload = EmailSignupRequest(
        email="newuser@example.com",
        password="ValidPassword123!",
        firstName="Test",
        lastName="User",
        role="user",
        firebaseId="fb_uid_123",
        device_id="device-exceeded",
    )
    firebase_user = {"uid": "fb_uid_123", "email": "newuser@example.com"}

    with (
        patch(
            "apps.accounts.services.device_limit_service.validate_device_account_limit",
            AsyncMock(side_effect=ApiError("The maximum number of accounts allowed on this device has been reached.")),
        ),
        patch(
            "apps.accounts.services.registration_service.delete_firebase_user_safely",
            Mock(),
        ),
    ):
        with pytest.raises(ApiError) as exc:
            await signup(payload, firebase_user, db)

    assert "The maximum number of accounts allowed on this device has been reached." in str(exc.value)


@pytest.mark.asyncio
async def test_login_returns_account_limit_exceeded():
    from apps.accounts.db_models import User
    from apps.accounts.services.common_service import PASSWORD_HASHER

    user = User(
        id=uuid4(),
        email="existing@example.com",
        firebase_uid="fb_uid_456",
        password_hash=PASSWORD_HASHER.hash("ValidPassword123!"),
    )

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=mock_result)

    payload = LoginRequest(
        email="existing@example.com",
        password="ValidPassword123!",
        firebaseId="fb_uid_456",
        device_id="device-exceeded",
    )
    firebase_user = {"uid": "fb_uid_456", "email": "existing@example.com"}

    with patch(
        "apps.accounts.services.device_limit_service.validate_device_account_limit",
        AsyncMock(side_effect=ApiError("The maximum number of accounts allowed on this device has been reached.")),
    ):
        with pytest.raises(ApiError) as exc:
            await login(payload, firebase_user, db)

    assert "The maximum number of accounts allowed on this device has been reached." in str(exc.value)
