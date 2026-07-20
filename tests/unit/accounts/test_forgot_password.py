from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.accounts.schemas import ForgotPasswordRequest


@pytest.mark.asyncio
async def test_forgot_password_uses_firebase_native_email(monkeypatch, db_scalar_result):
    from apps.accounts.services import forgot_password

    user = MagicMock()
    user.id = uuid4()
    user.email = "user@example.com"

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

    user = MagicMock(spec=User)
    user.id = uuid4()
    user.email = "user@example.com"

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
