from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.chat.router import get_current_chat_user
from apps.chat.service import (
    StreamChatError,
    build_stream_user_payload,
    deactivate_stream_user,
    deactivate_stream_user_best_effort,
    delete_stream_user,
    delete_stream_user_best_effort,
    generate_stream_token,
    reactivate_stream_user,
    reactivate_stream_user_best_effort,
    revoke_stream_user_tokens,
    revoke_stream_user_tokens_best_effort,
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
    assert payload["name"] == "Jane Doe"
    assert payload["image"] is not None
    assert "profiles/photo.jpg" in payload["image"]


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
async def test_generate_stream_token_returns_non_expiring_token(monkeypatch) -> None:
    user = User(
        id=uuid4(),
        email="user@example.com",
        firebase_uid="firebase-uid",
    )
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")

    mock_stream_client = MagicMock()
    mock_stream_client.create_token.return_value = "stream-token-123"
    monkeypatch.setattr("apps.chat.service._stream_client", mock_stream_client)

    with patch(
        "apps.chat.service.get_stream_client",
        return_value=mock_stream_client,
    ):
        token_data = await generate_stream_token(user)

    assert token_data.stream_token == "stream-token-123"
    mock_stream_client.create_token.assert_called_once_with(str(user.id))


@pytest.mark.asyncio
async def test_revoke_stream_user_tokens_calls_stream_api(monkeypatch) -> None:
    user = User(
        id=uuid4(),
        email="user@example.com",
        firebase_uid="firebase-uid",
    )
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")

    mock_stream_client = MagicMock()
    with patch(
        "apps.chat.service.get_stream_client",
        return_value=mock_stream_client,
    ):
        await revoke_stream_user_tokens(user)

    mock_stream_client.revoke_user_token.assert_called_once()
    args, _kwargs = mock_stream_client.revoke_user_token.call_args
    assert args[0] == str(user.id)


@pytest.mark.asyncio
async def test_revoke_stream_user_tokens_best_effort_does_not_raise(monkeypatch) -> None:
    user = User(
        id=uuid4(),
        email="user@example.com",
        firebase_uid="firebase-uid",
    )
    with patch(
        "apps.chat.service.revoke_stream_user_tokens",
        new=AsyncMock(side_effect=StreamChatError("not configured")),
    ):
        await revoke_stream_user_tokens_best_effort(user)


@pytest.mark.asyncio
async def test_deactivate_stream_user_success(monkeypatch) -> None:
    user = User(id=uuid4(), email="user@example.com", firebase_uid="firebase-uid")
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")
    mock_stream_client = MagicMock()

    with (
        patch("apps.chat.service.get_stream_client", return_value=mock_stream_client),
        patch(
            "apps.chat.service.revoke_stream_user_tokens_best_effort",
            new=AsyncMock(),
        ) as revoke,
    ):
        await deactivate_stream_user(user)

    mock_stream_client.deactivate_user.assert_called_once_with(str(user.id))
    revoke.assert_awaited_once_with(user)


@pytest.mark.asyncio
async def test_deactivate_stream_user_wraps_generic_errors(monkeypatch) -> None:
    user = User(id=uuid4(), email="user@example.com", firebase_uid="firebase-uid")
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")
    mock_stream_client = MagicMock()
    mock_stream_client.deactivate_user.side_effect = RuntimeError("down")

    with patch("apps.chat.service.get_stream_client", return_value=mock_stream_client):
        with pytest.raises(StreamChatError, match="Failed to deactivate Stream user"):
            await deactivate_stream_user(user)


@pytest.mark.asyncio
async def test_deactivate_stream_user_best_effort_swallows_errors() -> None:
    user = User(id=uuid4(), email="user@example.com", firebase_uid="firebase-uid")
    with patch(
        "apps.chat.service.deactivate_stream_user",
        new=AsyncMock(side_effect=StreamChatError("skip")),
    ):
        await deactivate_stream_user_best_effort(user)


@pytest.mark.asyncio
async def test_deactivate_stream_user_best_effort_swallows_unexpected_errors() -> None:
    user = User(id=uuid4(), email="user@example.com", firebase_uid="firebase-uid")
    with patch(
        "apps.chat.service.deactivate_stream_user",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await deactivate_stream_user_best_effort(user)


@pytest.mark.asyncio
async def test_reactivate_stream_user_success(monkeypatch) -> None:
    user = User(id=uuid4(), email="user@example.com", firebase_uid="firebase-uid")
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")
    mock_stream_client = MagicMock()
    db = AsyncMock()

    with (
        patch("apps.chat.service.get_stream_client", return_value=mock_stream_client),
        patch("apps.chat.service.upsert_stream_user", new=AsyncMock()) as upsert,
    ):
        await reactivate_stream_user(user, db)

    mock_stream_client.reactivate_user.assert_called_once_with(str(user.id))
    upsert.assert_awaited_once_with(user, db)


@pytest.mark.asyncio
async def test_reactivate_stream_user_best_effort_swallows_errors() -> None:
    user = User(id=uuid4(), email="user@example.com", firebase_uid="firebase-uid")
    with patch(
        "apps.chat.service.reactivate_stream_user",
        new=AsyncMock(side_effect=StreamChatError("skip")),
    ):
        await reactivate_stream_user_best_effort(user, AsyncMock())


@pytest.mark.asyncio
async def test_reactivate_stream_user_wraps_generic_errors(monkeypatch) -> None:
    user = User(id=uuid4(), email="user@example.com", firebase_uid="firebase-uid")
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")
    mock_stream_client = MagicMock()
    mock_stream_client.reactivate_user.side_effect = RuntimeError("down")

    with patch("apps.chat.service.get_stream_client", return_value=mock_stream_client):
        with pytest.raises(StreamChatError, match="Failed to reactivate Stream user"):
            await reactivate_stream_user(user, AsyncMock())


@pytest.mark.asyncio
async def test_delete_stream_user_success(monkeypatch) -> None:
    user_id = str(uuid4())
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")
    mock_stream_client = MagicMock()

    with patch("apps.chat.service.get_stream_client", return_value=mock_stream_client):
        await delete_stream_user(user_id)

    mock_stream_client.delete_user.assert_called_once_with(user_id)


@pytest.mark.asyncio
async def test_delete_stream_user_wraps_generic_errors(monkeypatch) -> None:
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")
    mock_stream_client = MagicMock()
    mock_stream_client.delete_user.side_effect = RuntimeError("down")

    with patch("apps.chat.service.get_stream_client", return_value=mock_stream_client):
        with pytest.raises(StreamChatError, match="Failed to delete Stream user"):
            await delete_stream_user(str(uuid4()))


@pytest.mark.asyncio
async def test_delete_stream_user_best_effort_swallows_errors() -> None:
    with patch(
        "apps.chat.service.delete_stream_user",
        new=AsyncMock(side_effect=StreamChatError("skip")),
    ):
        await delete_stream_user_best_effort(str(uuid4()))


@pytest.mark.asyncio
async def test_delete_stream_user_best_effort_swallows_unexpected_errors() -> None:
    with patch(
        "apps.chat.service.delete_stream_user",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await delete_stream_user_best_effort(str(uuid4()))


@pytest.mark.asyncio
async def test_upsert_stream_user_raises_on_api_failure(monkeypatch) -> None:
    user = User(
        id=uuid4(),
        email="user@example.com",
        firebase_uid="firebase-uid",
    )
    monkeypatch.setattr("apps.chat.service.settings.stream_api_key", "test-api-key")
    monkeypatch.setattr("apps.chat.service.settings.stream_secret_key", "test-secret-key")

    mock_stream_client = MagicMock()
    mock_stream_client.upsert_user.side_effect = RuntimeError("stream unavailable")

    with patch(
        "apps.chat.service.get_stream_client",
        return_value=mock_stream_client,
    ), patch(
        "apps.chat.service._fetch_user_profile",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(StreamChatError, match="Failed to sync user with Stream Chat"):
            from apps.chat.service import upsert_stream_user

            await upsert_stream_user(user, AsyncMock())


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
    async def _mock_generate_stream_token(_user):
        from apps.chat.schemas import StreamTokenData

        return StreamTokenData(stream_token="stream-token-123")

    monkeypatch.setattr(
        "apps.chat.router.generate_stream_token",
        _mock_generate_stream_token,
    )

    response = client.post(
        "/api/v1/chat/token",
        headers={"Authorization": "Bearer firebase-token"},
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Stream token generated successfully."
    assert body["data"]["stream_token"] == "stream-token-123"
    assert "expires_in" not in body["data"]


def test_create_stream_token_route_handles_service_error(monkeypatch) -> None:
    async def _mock_generate_stream_token(_user):
        raise StreamChatError("Stream Chat secret key is not configured")

    monkeypatch.setattr(
        "apps.chat.router.generate_stream_token",
        _mock_generate_stream_token,
    )

    response = client.post(
        "/api/v1/chat/token",
        headers={"Authorization": "Bearer firebase-token"},
        json={},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Stream Chat secret key is not configured"
    assert body["data"] is None


@pytest.mark.asyncio
async def test_get_current_chat_user_rejects_deleting_account() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from apps.chat.router import get_current_chat_user
    from common.enums import UserStatus
    from common.exceptions import ApiError

    user = SimpleNamespace(
        id=uuid4(),
        status=UserStatus.deleting,
        deleted_at=None,
        roles=[],
    )
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(ApiError):
        await get_current_chat_user({"uid": "firebase-uid"}, db)


@pytest.mark.asyncio
async def test_get_current_chat_user_rejects_missing_uid() -> None:
    from apps.chat.router import get_current_chat_user
    from common.exceptions import ApiError

    with pytest.raises(ApiError, match="Invalid Firebase credentials"):
        await get_current_chat_user({}, AsyncMock())
