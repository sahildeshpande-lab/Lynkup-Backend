# tests/unit/administration/test_admin_auth.py
"""Tests for admin session cookie authentication, CSRF protection, and MFA checks."""

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import get_session
from apps.accounts.db_models import User
from core.security.admin import _csrf_store, get_current_admin_user, router as admin_router


test_app = FastAPI()
test_app.include_router(admin_router, prefix="/api/v1")


async def mock_get_session():
    yield MagicMock(spec=AsyncSession)


test_app.dependency_overrides[get_session] = mock_get_session


@test_app.get("/api/v1/auth/admin-protected")
async def admin_protected_route(current_user: User = Depends(get_current_admin_user)):
    return {"status": "success", "email": current_user.email}


client = TestClient(test_app)


@pytest.fixture(autouse=True)
def clear_csrf_store():
    _csrf_store.clear()
    yield


def test_create_session_cookie_success(monkeypatch):
    mock_decoded = {
        "uid": "admin-uid",
        "role": "superadmin",
        "firebase": {"sign_in_second_factor": "phone"},
    }
    monkeypatch.setattr("core.security.admin.verify_firebase_token", lambda *args, **kwargs: mock_decoded)
    monkeypatch.setattr(
        "core.security.admin.firebase_auth.create_session_cookie",
        lambda *args, **kwargs: "mock-session-cookie",
    )

    response = client.post("/api/v1/auth/admin-cookie", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 200
    assert "csrf_token" in response.json()
    assert "admin_session" in response.cookies


def test_create_session_cookie_missing_token():
    response = client.post("/api/v1/auth/admin-cookie")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing ID token"


def test_create_session_cookie_insufficient_permissions(monkeypatch):
    mock_decoded = {
        "uid": "user-uid",
        "role": "user",
        "firebase": {"sign_in_second_factor": "phone"},
    }
    monkeypatch.setattr("core.security.admin.verify_firebase_token", lambda *args, **kwargs: mock_decoded)

    response = client.post("/api/v1/auth/admin-cookie", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


def test_create_session_cookie_mfa_required(monkeypatch):
    mock_decoded = {"uid": "admin-uid", "role": "admin", "firebase": {}}
    monkeypatch.setattr("core.security.admin.verify_firebase_token", lambda *args, **kwargs: mock_decoded)

    response = client.post("/api/v1/auth/admin-cookie", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 403
    assert response.json()["detail"] == "MFA required"


@pytest.mark.asyncio
async def test_get_current_admin_user_success(monkeypatch):
    mock_decoded_cookie = {"uid": "admin-uid"}
    monkeypatch.setattr(
        "core.security.admin.firebase_auth.verify_session_cookie",
        lambda *args, **kwargs: mock_decoded_cookie,
    )

    mock_user = User(email="admin_user@example.com", firebase_uid="admin-uid")
    monkeypatch.setattr(User, "role", "admin")

    async def mock_get_user(db, uid):
        return mock_user

    monkeypatch.setattr("core.security.admin.get_user_by_firebase_uid", mock_get_user)

    _csrf_store["admin_session:valid-cookie"] = "valid-csrf-token"

    client.cookies.set("admin_session", "valid-cookie")
    response = client.get("/api/v1/auth/admin-protected", headers={"X-CSRF-Token": "valid-csrf-token"})

    assert response.status_code == 200
    assert response.json() == {"status": "success", "email": "admin_user@example.com"}


@pytest.mark.asyncio
async def test_get_current_admin_user_invalid_csrf(monkeypatch):
    mock_decoded_cookie = {"uid": "admin-uid"}
    monkeypatch.setattr(
        "core.security.admin.firebase_auth.verify_session_cookie",
        lambda *args, **kwargs: mock_decoded_cookie,
    )

    _csrf_store["admin_session:valid-cookie"] = "valid-csrf-token"

    client.cookies.set("admin_session", "valid-cookie")
    response = client.get("/api/v1/auth/admin-protected", headers={"X-CSRF-Token": "invalid-csrf-token"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid CSRF token"
