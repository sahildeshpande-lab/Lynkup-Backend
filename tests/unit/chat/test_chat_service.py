from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.chat.dependencies import get_current_chat_user
from apps.chat.service import (
    StreamChatError,
    build_stream_user_payload,
    generate_stream_token,
    sync_stream_user_on_auth,
)
from apps.profiles.db_models.profile_db_model import Profile
from core.auth.firebase import get_current_firebase_user
from entrypoints.api import app

client = TestClient(app)


def test_build_stream_user_payload_uses_profile_fields() -> None:
    user = User(
        id=uuid4(),
        email="user@example.com",
        firebase_uid="firebase-uid",
    )
    profile = Profile(
        user_id=user.id,
        first_name="Jane",
        last_name="Doe",
        profile_photo_url="profiles/photo.jpg",
    )

    payload = build_stream_user_payload(user, profile)

    assert payload["id"] == str(user.id)
    assert payload["full_name"] == "Jane Doe"
    assert payload["profile_photo_url"] is not None
    assert "profiles/photo.jpg" in payload["profile_photo_url"]


@pytest.mark.asyncio
async def test_sync_stream_user_on_auth_does_not_raise_on_failure() -> None:
    user = User(
        id=uuid4(),
        email="user@example.com",
        firebase_uid="firebase-uid",
    )

    with patch(
        "apps.chat.service.upsert_stream_user",
        new=AsyncMock(side_effect=StreamChatError("Stream Chat API key is not configured")),
    ):
        await sync_stream_user_on_auth(user, AsyncMock())


@pytest.mark.asyncio
async def test_generate_stream_token_uses_configured_expiry(monkeypatch) -> None:
    user = User(
        id=uuid4(),
        email="user@example.com",
        firebase_uid="firebase-uid",
    )
    monkeypatch.setattr("apps.chat.service.settings.stream_token_expiry", 7200)
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")

    mock_stream_client = MagicMock()
    mock_stream_client.create_token.return_value = "stream-token-123"

    @asynccontextmanager
    async def mock_stream_client_context():
        yield mock_stream_client

    with patch(
        "apps.chat.service.upsert_stream_user",
        new=AsyncMock(),
    ), patch(
        "apps.chat.service._stream_client",
        mock_stream_client_context,
    ), patch(
        "apps.chat.service.time.time",
        return_value=1_700_000_000,
    ):
        token_data = await generate_stream_token(user, AsyncMock())

    assert token_data.stream_token == "stream-token-123"
    assert token_data.expires_in == 7200
    mock_stream_client.create_token.assert_called_once_with(
        str(user.id),
        exp=1_700_000_000 + 7200,
    )


async def _override_firebase_user():
    return {"uid": "firebase-uid", "email": "user@example.com"}


async def _override_chat_user():
    user = User(
        id="11111111-1111-1111-1111-111111111111",
        email="user@example.com",
        firebase_uid="firebase-uid",
    )
    user.role = "user"
    return user


def setup_module() -> None:
    app.dependency_overrides[get_current_firebase_user] = _override_firebase_user
    app.dependency_overrides[get_current_chat_user] = _override_chat_user


def teardown_module() -> None:
    app.dependency_overrides.pop(get_current_firebase_user, None)
    app.dependency_overrides.pop(get_current_chat_user, None)


def test_create_stream_token_route_success(monkeypatch) -> None:
    async def _mock_generate_stream_token(_user, _db):
        from apps.chat.schemas import StreamTokenData

        return StreamTokenData(stream_token="stream-token-123", expires_in=86400)

    monkeypatch.setattr(
        "apps.chat.router.generate_stream_token",
        _mock_generate_stream_token,
    )

    response = client.post(
        "/api/v1/chat/token",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Stream token generated successfully."
    assert body["data"]["stream_token"] == "stream-token-123"
    assert body["data"]["expires_in"] == 86400


def test_create_stream_token_route_handles_service_error(monkeypatch) -> None:
    async def _mock_generate_stream_token(_user, _db):
        raise StreamChatError("Stream Chat secret key is not configured")

    monkeypatch.setattr(
        "apps.chat.router.generate_stream_token",
        _mock_generate_stream_token,
    )

    response = client.post(
        "/api/v1/chat/token",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Stream Chat secret key is not configured"
    assert body["data"] is None
