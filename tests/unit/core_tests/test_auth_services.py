from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from core.auth import services as auth_services


def test_create_firebase_user_delegates_to_firebase_admin(monkeypatch) -> None:
    captured = {}

    def _mock_create_user(**kwargs):
        captured.update(kwargs)
        user = MagicMock()
        user.uid = "firebase-uid-123"
        return user

    monkeypatch.setattr(auth_services, "initialize_firebase_app", lambda: None)
    monkeypatch.setattr(auth_services.auth, "create_user", _mock_create_user)

    result = auth_services.create_firebase_user(
        email="user@example.com",
        password="Secret123!",
        display_name="Test User",
    )

    assert result.uid == "firebase-uid-123"
    assert captured["email"] == "user@example.com"
    assert captured["password"] == "Secret123!"
    assert captured["display_name"] == "Test User"
    assert captured["email_verified"] is True


def test_delete_firebase_user_delegates_to_firebase_admin(monkeypatch) -> None:
    deleted = []

    monkeypatch.setattr(auth_services, "initialize_firebase_app", lambda: None)
    monkeypatch.setattr(
        auth_services.auth,
        "delete_user",
        lambda uid: deleted.append(uid),
    )

    auth_services.delete_firebase_user("firebase-uid-123")

    assert deleted == ["firebase-uid-123"]


def test_update_firebase_password_delegates_to_firebase_admin(monkeypatch) -> None:
    updated = []

    monkeypatch.setattr(auth_services, "initialize_firebase_app", lambda: None)
    monkeypatch.setattr(
        auth_services.auth,
        "update_user",
        lambda uid, **kwargs: updated.append((uid, kwargs)),
    )

    auth_services.update_firebase_password("firebase-uid-123", "NewPassword123!")

    assert updated == [("firebase-uid-123", {"password": "NewPassword123!"})]


def test_revoke_firebase_tokens_delegates_to_firebase_admin(monkeypatch) -> None:
    revoked = []

    monkeypatch.setattr(auth_services, "initialize_firebase_app", lambda: None)
    monkeypatch.setattr(
        auth_services.auth,
        "revoke_refresh_tokens",
        lambda uid: revoked.append(uid),
    )

    auth_services.revoke_firebase_tokens("firebase-uid-123")

    assert revoked == ["firebase-uid-123"]


def test_verify_firebase_token_delegates_to_firebase(monkeypatch) -> None:
    monkeypatch.setattr(auth_services, "initialize_firebase_app", lambda: None)
    monkeypatch.setattr(
        auth_services.auth,
        "verify_id_token",
        lambda token, check_revoked=False: {"uid": "uid-1"},
    )

    assert auth_services.verify_firebase_token("token") == {"uid": "uid-1"}


def test_disable_and_enable_firebase_user(monkeypatch) -> None:
    updates = []
    revoked = []

    monkeypatch.setattr(auth_services, "initialize_firebase_app", lambda: None)
    monkeypatch.setattr(
        auth_services.auth,
        "update_user",
        lambda uid, **kwargs: updates.append((uid, kwargs)),
    )
    monkeypatch.setattr(
        auth_services.auth,
        "revoke_refresh_tokens",
        lambda uid: revoked.append(uid),
    )

    auth_services.disable_firebase_user("firebase-uid-123")
    auth_services.enable_firebase_user("firebase-uid-456")

    assert updates == [
        ("firebase-uid-123", {"disabled": True}),
        ("firebase-uid-456", {"disabled": False}),
    ]
    assert revoked == ["firebase-uid-123"]
