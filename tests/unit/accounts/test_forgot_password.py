from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, status

from apps.accounts.schemas import ForgotPasswordRequest


@pytest.mark.asyncio
async def test_forgot_password_uses_firebase_native_email(monkeypatch, db_scalar_result):
    from apps.accounts.services import forgot_password

    user = MagicMock()
    user.id = uuid4()
    user.email = "user@example.com"

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalar_result(user))
    send_calls: list[str] = []

    async def mock_send_firebase_password_reset_email(email: str) -> None:
        send_calls.append(email)

    monkeypatch.setattr(
        "apps.accounts.services._send_firebase_password_reset_email",
        mock_send_firebase_password_reset_email,
    )

    payload = ForgotPasswordRequest(email="USER@example.com", firebaseId="firebase-token")
    result = await forgot_password(payload, mock_db)

    assert result.status is True
    assert result.message == "Password reset email sent successfully"
    assert result.data is None
    assert send_calls == ["user@example.com"]
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_firebase_password_reset_failure_raises_502(monkeypatch):
    from apps.accounts import services

    class FakeResponse:
        status_code = 400
        text = '{"error":{"message":"INVALID_EMAIL"}}'

        def raise_for_status(self):
            request = httpx.Request("POST", "https://firebase.test")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            return FakeResponse()

    monkeypatch.setattr(services.auth_settings, "firebase_web_api_key", "test-api-key")
    monkeypatch.setattr(services.httpx, "AsyncClient", FakeClient)

    with pytest.raises(HTTPException) as exc_info:
        await services._send_firebase_password_reset_email("user@example.com")

    assert exc_info.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc_info.value.detail == "Failed to send Firebase password reset email"
