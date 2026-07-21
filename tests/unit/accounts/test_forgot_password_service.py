from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.accounts.schemas import ForgotPasswordRequest
from apps.accounts.services import password_service as svc


def _user(email: str = "user@example.com"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=email,
    )


@pytest.mark.asyncio
async def test_build_password_reset_link_uses_base_url_fallback(monkeypatch):
    monkeypatch.delenv("APPLICATION_LINK", raising=False)
    monkeypatch.setattr(svc.email_settings, "base_url", "https://app.example.test")

    link = svc._build_password_reset_link("token-123")

    assert link == "https://app.example.test/reset-password?token=token-123"


@pytest.mark.asyncio
async def test_forgot_password_commits_only_after_email_succeeds(monkeypatch, mock_db, scalar_result):
    user = _user()
    db = mock_db(scalar_result(user), scalar_result(None))

    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(svc, "send_reset_password_email", send_email)

    response = await svc.forgot_password(
        ForgotPasswordRequest(email=user.email),
        db,
    )

    assert response.status is True
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()
    send_email.assert_awaited_once()
    assert "reset-password?token=" in send_email.await_args.args[1]


@pytest.mark.asyncio
async def test_forgot_password_rolls_back_token_when_email_fails(monkeypatch, mock_db, scalar_result):
    user = _user()
    db = mock_db(scalar_result(user), scalar_result(None))

    monkeypatch.setattr(svc, "send_reset_password_email", AsyncMock(return_value=False))

    response = await svc.forgot_password(
        ForgotPasswordRequest(email=user.email),
        db,
    )

    assert response.status is False
    assert "Failed to send password reset email" in response.message
    db.rollback.assert_awaited_once()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_forgot_password_rate_limited_when_active_token_exists(monkeypatch, mock_db, scalar_result):
    user = _user()
    existing = SimpleNamespace(
        expires_at=datetime.now(timezone.utc),
    )
    db = mock_db(scalar_result(user), scalar_result(existing))
    send_email = AsyncMock()
    monkeypatch.setattr(svc, "send_reset_password_email", send_email)

    response = await svc.forgot_password(
        ForgotPasswordRequest(email=user.email),
        db,
    )

    assert response.status is False
    assert "Recently email for reset password" in response.message
    send_email.assert_not_called()
