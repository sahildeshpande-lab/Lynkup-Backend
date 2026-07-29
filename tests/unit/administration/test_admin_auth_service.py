from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from apps.accounts.schemas import RefreshTokenRequest
from apps.administration import routes as admin_routes
from apps.administration.schemas import AdminLoginRequest
from apps.administration.services.auth_service import (
    _ensure_admin_user_can_authenticate,
    admin_signin,
    admin_token,
)
from apps.accounts.services import JWT_ALGORITHM, JWT_SECRET
from common.enums import UserStatus, inactive_account_message
from common.exceptions import ApiError
from entrypoints.api import app

client = TestClient(app)


def test_ensure_admin_user_can_authenticate_rejects_deleting() -> None:
    user = SimpleNamespace(
        status=UserStatus.deleting,
        deleted_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ApiError) as exc:
        _ensure_admin_user_can_authenticate(user)

    assert exc.value.message == inactive_account_message(UserStatus.deleting)


def test_ensure_admin_user_can_authenticate_rejects_deleted_at_only() -> None:
    user = SimpleNamespace(
        status=UserStatus.active,
        deleted_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ApiError) as exc:
        _ensure_admin_user_can_authenticate(user)

    assert exc.value.message == inactive_account_message(UserStatus.deleting)


@pytest.mark.asyncio
async def test_admin_signin_rejects_deleting_moderator(monkeypatch) -> None:
    email = "moderator@example.com"
    user = SimpleNamespace(
        email=email,
        password_hash="hashed",
        role="moderator",
        status=UserStatus.deleting,
        deleted_at=datetime.now(timezone.utc),
        id=uuid4(),
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))

    monkeypatch.setattr(
        "apps.administration.services.auth_service.PASSWORD_HASHER.verify",
        lambda _password, _hash: True,
    )

    with pytest.raises(ApiError) as exc:
        await admin_signin(AdminLoginRequest(email=email, password="secret"), db)

    assert exc.value.message == inactive_account_message(UserStatus.deleting)


@pytest.mark.asyncio
async def test_admin_token_rejects_deleting_superadmin() -> None:
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        status=UserStatus.deleting,
        deleted_at=datetime.now(timezone.utc),
        firebase_uid="admin-uid",
        email="admin@example.com",
        role="superadmin",
    )
    refresh_token = jwt.encode(
        {"sub": str(user_id), "type": "refresh"},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user))

    with pytest.raises(ApiError) as exc:
        await admin_token(RefreshTokenRequest(refreshToken=refresh_token), db)

    assert exc.value.message == inactive_account_message(UserStatus.deleting)


def test_admin_login_route_returns_401_for_deleting_account(monkeypatch) -> None:
    async def _mock_admin_signin(payload, db):
        raise ApiError(inactive_account_message(UserStatus.deleting))

    monkeypatch.setattr(admin_routes.services, "admin_signin", _mock_admin_signin)

    response = client.post(
        "/api/v1/auth/admin/login",
        json={"email": "moderator@example.com", "password": "secret"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["status"] is False
    assert body["message"] == inactive_account_message(UserStatus.deleting)
