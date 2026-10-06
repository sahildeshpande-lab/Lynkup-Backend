from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest

from apps.connections.services.connection_service import respond_connection_request
from core.database import get_session
from core.security.auth import get_current_user
from entrypoints.api import app


@pytest.mark.asyncio
async def test_lynkupresponse_invalid_user_id_format_returns_not_a_valid_user() -> None:
    viewer = SimpleNamespace(id=uuid.uuid4())

    async def _override_user():
        return viewer

    app.dependency_overrides[get_current_user] = _override_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/lynkupresponse",
                json={
                    "receiver_user_id": "sjdnjddjwn-aejwndjwd",
                    "response": "accepted",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Not a valid User"
    assert body["data"] is None


@pytest.mark.asyncio
async def test_respond_connection_request_unknown_user_returns_not_a_valid_user(mock_db) -> None:
    db = mock_db()
    result = await respond_connection_request(
        db,
        uuid.uuid4(),
        uuid.uuid4(),
        "accepted",
    )

    assert result.status is False
    assert result.message == "Not a valid User"
    assert result.data is None


@pytest.mark.asyncio
async def test_lynkupresponse_unknown_user_id_returns_not_a_valid_user(mock_db) -> None:
    viewer = SimpleNamespace(id=uuid.uuid4())
    db = mock_db()

    async def _override_user():
        return viewer

    async def _override_db():
        return db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/lynkupresponse",
                json={
                    "receiver_user_id": str(uuid.uuid4()),
                    "response": "accepted",
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Not a valid User"
    assert body["data"] is None
