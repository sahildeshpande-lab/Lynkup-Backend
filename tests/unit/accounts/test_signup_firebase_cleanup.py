"""Tests for Firebase cleanup on failed /auth/signup attempts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from apps.accounts.db_models import User
from apps.accounts.schemas import EmailSignupRequest
from apps.accounts.services import registration_service as reg_svc
from apps.accounts.services.common_service import (
    PUBLIC_AUTH_ACCOUNT_EXISTS_MESSAGE,
    SIGNUP_GENERIC_FAILURE_MESSAGE,
    STAFF_PUBLIC_AUTH_NOT_ALLOWED_MESSAGE,
)
from common.enums import RegistrationType, UserStatus
from common.exceptions import ApiError
from tests.unit.conftest import FakeScalarResult


def _signup_payload(email: str = "user@example.com") -> EmailSignupRequest:
    return EmailSignupRequest(
        firstName="Jane",
        lastName="Doe",
        email=email,
        password="Secret123",
        role="user",
        firebaseId="valid-firebase-id-token",
    )


def _firebase_user(*, uid: str | None = "new-firebase-uid", email: str = "user@example.com") -> dict:
    return {"uid": uid, "email": email}


def _staff_roles(role_name: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(role=SimpleNamespace(name=role_name))]


def _new_signup_db() -> Mock:
    added: list[object] = []
    lookups_remaining = 2

    async def execute(_statement):
        nonlocal lookups_remaining
        if lookups_remaining > 0:
            lookups_remaining -= 1
            return FakeScalarResult(None)
        users = [item for item in added if isinstance(item, User)]
        user = users[-1] if users else None
        if user is not None and not hasattr(user, "roles"):
            user.roles = []
        return FakeScalarResult(user)

    db = Mock()
    db.add = added.append
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _existing_email_db(existing: SimpleNamespace) -> Mock:
    db = Mock()
    db.execute = AsyncMock(
        side_effect=[
            FakeScalarResult(None),
            FakeScalarResult(existing),
            FakeScalarResult(values=[]),
            FakeScalarResult(existing),
        ]
    )
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.add = Mock()
    return db


@pytest.fixture
def signup_success_patches(monkeypatch):
    monkeypatch.setattr(reg_svc, "assign_user_role", AsyncMock())
    monkeypatch.setattr(reg_svc, "begin_otp_challenge", AsyncMock(return_value=True))
    monkeypatch.setattr(
        reg_svc,
        "_issue_auth_session",
        AsyncMock(return_value={"user": {"email": "user@example.com"}}),
    )
    monkeypatch.setattr(
        "apps.profiles.services.calculate_completeness_score",
        AsyncMock(return_value=10),
    )
    monkeypatch.setattr(
        "apps.accounts.services.registration_service.delete_firebase_user_safely",
        Mock(),
    )


@pytest.mark.asyncio
async def test_successful_signup_does_not_delete_firebase_user(signup_success_patches) -> None:
    delete_mock = reg_svc.delete_firebase_user_safely
    db = _new_signup_db()

    result = await reg_svc.signup(
        _signup_payload(),
        _firebase_user(),
        db,
    )

    assert result.status is True
    delete_mock.assert_not_called()


@pytest.mark.asyncio
async def test_existing_normal_user_firebase_uid_does_not_delete_firebase_user() -> None:
    existing_uid = "existing-user-firebase-uid"
    existing = SimpleNamespace(
        id=uuid4(),
        email="user@example.com",
        firebase_uid=existing_uid,
        registration_type=RegistrationType.email,
        status=UserStatus.active,
        roles=[],
    )
    db = Mock()
    db.execute = AsyncMock(side_effect=[FakeScalarResult(existing)])
    db.rollback = AsyncMock()
    delete_mock = Mock()

    with patch(
        "apps.accounts.services.registration_service.delete_firebase_user_safely",
        delete_mock,
    ):
        with pytest.raises(ApiError, match=PUBLIC_AUTH_ACCOUNT_EXISTS_MESSAGE):
            await reg_svc.signup(
                _signup_payload(),
                _firebase_user(uid=existing_uid),
                db,
            )

    db.rollback.assert_awaited_once()
    delete_mock.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role_name", ["moderator", "viewer", "superadmin"])
async def test_existing_staff_email_deletes_new_firebase_uid_only(role_name: str) -> None:
    old_uid = "existing-staff-firebase-uid"
    new_uid = "new-firebase-uid"
    existing = SimpleNamespace(
        id=uuid4(),
        email="staff@example.com",
        firebase_uid=old_uid,
        registration_type=RegistrationType.email,
        status=UserStatus.active,
        deleted_at=None,
        roles=_staff_roles(role_name),
    )
    db = _existing_email_db(existing)
    delete_mock = Mock()

    with patch(
        "apps.accounts.services.registration_service.delete_firebase_user_safely",
        delete_mock,
    ):
        with pytest.raises(ApiError, match=STAFF_PUBLIC_AUTH_NOT_ALLOWED_MESSAGE):
            await reg_svc.signup(
                _signup_payload("staff@example.com"),
                _firebase_user(uid=new_uid, email="staff@example.com"),
                db,
            )

    db.rollback.assert_awaited_once()
    delete_mock.assert_called_once_with(new_uid)
    assert existing.firebase_uid == old_uid


@pytest.mark.asyncio
async def test_disposable_email_validation_deletes_firebase_user() -> None:
    db = Mock()
    db.rollback = AsyncMock()
    delete_mock = Mock()

    with (
        patch(
            "common.email_validation.validate_disposable_email",
            side_effect=ApiError("Disposable email addresses are not allowed."),
        ),
        patch(
            "apps.accounts.services.registration_service.delete_firebase_user_safely",
            delete_mock,
        ),
    ):
        with pytest.raises(ApiError):
            await reg_svc.signup(_signup_payload(), _firebase_user(uid="uid-1"), db)

    db.rollback.assert_awaited_once()
    delete_mock.assert_called_once_with("uid-1")


@pytest.mark.asyncio
async def test_user_creation_failure_deletes_firebase_user(signup_success_patches) -> None:
    db = _new_signup_db()
    delete_mock = reg_svc.delete_firebase_user_safely

    with patch.object(db, "flush", AsyncMock(side_effect=RuntimeError("db flush failed"))):
        result = await reg_svc.signup(_signup_payload(), _firebase_user(uid="uid-user"), db)

    assert result.status is False
    assert result.message == SIGNUP_GENERIC_FAILURE_MESSAGE
    db.rollback.assert_awaited_once()
    delete_mock.assert_called_once_with("uid-user")


@pytest.mark.asyncio
async def test_role_assignment_failure_deletes_firebase_user(signup_success_patches) -> None:
    db = _new_signup_db()
    delete_mock = reg_svc.delete_firebase_user_safely

    with patch.object(
        reg_svc,
        "assign_user_role",
        AsyncMock(side_effect=RuntimeError("role assignment failed")),
    ):
        result = await reg_svc.signup(_signup_payload(), _firebase_user(uid="uid-role"), db)

    assert result.status is False
    assert result.message == SIGNUP_GENERIC_FAILURE_MESSAGE
    db.rollback.assert_awaited_once()
    delete_mock.assert_called_once_with("uid-role")


@pytest.mark.asyncio
async def test_profile_creation_failure_deletes_firebase_user(signup_success_patches) -> None:
    db = _new_signup_db()
    delete_mock = reg_svc.delete_firebase_user_safely

    with patch(
        "apps.profiles.services.calculate_completeness_score",
        AsyncMock(side_effect=RuntimeError("profile failed")),
    ):
        result = await reg_svc.signup(_signup_payload(), _firebase_user(uid="uid-profile"), db)

    assert result.status is False
    assert result.message == SIGNUP_GENERIC_FAILURE_MESSAGE
    db.rollback.assert_awaited_once()
    delete_mock.assert_called_once_with("uid-profile")


@pytest.mark.asyncio
async def test_consent_failure_deletes_firebase_user(signup_success_patches) -> None:
    db = _new_signup_db()
    delete_mock = reg_svc.delete_firebase_user_safely

    with patch.object(
        reg_svc,
        "save_current_consent",
        AsyncMock(side_effect=RuntimeError("consent failed")),
    ):
        result = await reg_svc.signup(_signup_payload(), _firebase_user(uid="uid-consent"), db)

    assert result.status is False
    assert result.message == SIGNUP_GENERIC_FAILURE_MESSAGE
    db.rollback.assert_awaited_once()
    delete_mock.assert_called_once_with("uid-consent")


@pytest.mark.asyncio
async def test_otp_failure_deletes_firebase_user(signup_success_patches) -> None:
    db = _new_signup_db()
    delete_mock = reg_svc.delete_firebase_user_safely

    with patch.object(
        reg_svc,
        "begin_otp_challenge",
        AsyncMock(side_effect=RuntimeError("otp failed")),
    ):
        result = await reg_svc.signup(_signup_payload(), _firebase_user(uid="uid-otp"), db)

    assert result.status is False
    assert result.message == SIGNUP_GENERIC_FAILURE_MESSAGE
    db.rollback.assert_awaited_once()
    delete_mock.assert_called_once_with("uid-otp")


@pytest.mark.asyncio
async def test_session_failure_deletes_firebase_user(signup_success_patches) -> None:
    db = _new_signup_db()
    delete_mock = reg_svc.delete_firebase_user_safely

    with patch.object(
        reg_svc,
        "_issue_auth_session",
        AsyncMock(side_effect=RuntimeError("session failed")),
    ):
        result = await reg_svc.signup(_signup_payload(), _firebase_user(uid="uid-session"), db)

    assert result.status is False
    assert result.message == SIGNUP_GENERIC_FAILURE_MESSAGE
    db.rollback.assert_awaited_once()
    delete_mock.assert_called_once_with("uid-session")


@pytest.mark.asyncio
async def test_soft_deleted_email_within_grace_rejects_signup(signup_success_patches) -> None:
    now = datetime.now(timezone.utc)
    existing = SimpleNamespace(
        id=uuid4(),
        email="returning@example.com",
        firebase_uid="old-firebase-uid",
        registration_type=RegistrationType.email,
        status=UserStatus.deleting,
        deleted_at=now,
        is_deleted=True,
        purge_after=now + timedelta(days=30),
        password_hash="old-hash",
        updated_at=now,
        last_login_at=None,
        roles=[],
    )
    db = _existing_email_db(existing)
    delete_mock = reg_svc.delete_firebase_user_safely

    with pytest.raises(ApiError, match=PUBLIC_AUTH_ACCOUNT_EXISTS_MESSAGE):
        await reg_svc.signup(
            _signup_payload("returning@example.com"),
            _firebase_user(uid="new-firebase-uid", email="returning@example.com"),
            db,
        )

    delete_mock.assert_not_called()


@pytest.mark.asyncio
async def test_soft_deleted_email_after_grace_allows_new_signup(signup_success_patches) -> None:
    now = datetime.now(timezone.utc)
    existing = SimpleNamespace(
        id=uuid4(),
        email="returning@example.com",
        firebase_uid="old-firebase-uid",
        registration_type=RegistrationType.email,
        status=UserStatus.deleting,
        deleted_at=now - timedelta(days=40),
        is_deleted=True,
        purge_after=now - timedelta(days=1),
        password_hash="old-hash",
        updated_at=now,
        last_login_at=None,
        roles=[],
    )
    added: list[object] = []
    lookup_count = 0

    async def execute(_statement):
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            return FakeScalarResult(None)
        if lookup_count == 2:
            return FakeScalarResult(existing)
        users = [item for item in added if isinstance(item, User)]
        user = users[-1] if users else None
        if user is not None and not hasattr(user, "roles"):
            user.roles = []
        return FakeScalarResult(user)

    db = Mock()
    db.add = added.append
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    delete_mock = reg_svc.delete_firebase_user_safely

    with patch.object(
        reg_svc,
        "remove_expired_deleting_user_for_resignup",
        AsyncMock(),
    ) as purge_mock:
        result = await reg_svc.signup(
            _signup_payload("returning@example.com"),
            _firebase_user(uid="new-firebase-uid", email="returning@example.com"),
            db,
        )

    purge_mock.assert_awaited_once()
    assert result.status is True
    delete_mock.assert_not_called()


@pytest.mark.asyncio
async def test_firebase_deletion_failure_is_logged_without_masking_signup_error(
    caplog,
    monkeypatch,
) -> None:
    from core.auth import services as auth_services

    monkeypatch.setattr(reg_svc, "assign_user_role", AsyncMock())
    monkeypatch.setattr(
        "apps.profiles.services.calculate_completeness_score",
        AsyncMock(return_value=10),
    )
    db = _new_signup_db()

    with (
        patch.object(
            reg_svc,
            "save_current_consent",
            AsyncMock(side_effect=RuntimeError("consent failed")),
        ),
        patch.object(auth_services, "initialize_firebase_app", lambda: None),
        patch.object(
            auth_services.auth,
            "delete_user",
            side_effect=RuntimeError("firebase delete failed"),
        ),
    ):
        with caplog.at_level("ERROR"):
            result = await reg_svc.signup(
                _signup_payload(),
                _firebase_user(uid="uid-delete-fail"),
                db,
            )

    assert result.status is False
    assert result.message == SIGNUP_GENERIC_FAILURE_MESSAGE
    assert "Failed to delete Firebase user during signup cleanup" in caplog.text
    db.rollback.assert_awaited_once()


def test_delete_firebase_user_safely_ignores_missing_user(monkeypatch) -> None:
    from core.auth import services as auth_services

    monkeypatch.setattr(auth_services, "initialize_firebase_app", lambda: None)

    def _raise(_uid: str) -> None:
        raise auth_services.auth.UserNotFoundError("missing")

    monkeypatch.setattr(auth_services.auth, "delete_user", _raise)
    auth_services.delete_firebase_user_safely("missing-uid")


def test_delete_firebase_user_safely_logs_other_failures(monkeypatch, caplog) -> None:
    from core.auth import services as auth_services

    monkeypatch.setattr(auth_services, "initialize_firebase_app", lambda: None)

    def _raise(_uid: str) -> None:
        raise RuntimeError("firebase down")

    monkeypatch.setattr(auth_services.auth, "delete_user", _raise)

    with caplog.at_level("ERROR"):
        auth_services.delete_firebase_user_safely("uid-123")

    assert "Failed to delete Firebase user during signup cleanup" in caplog.text
