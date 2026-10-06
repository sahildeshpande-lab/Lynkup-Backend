from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from firebase_admin import auth as firebase_auth
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from apps.accounts.db_models import RefreshToken, User
from apps.accounts.schemas import ChangeEmailRequest
from apps.profiles.db_models import Profile
from common.enums import UserStatus
from common.exceptions import ApiError


def _graduated_profile(*, graduation_date: date | None = None) -> Profile:
    return Profile(
        user_id=uuid4(),
        first_name="Grad",
        last_name="Student",
        graduation_date=graduation_date or (datetime.now(timezone.utc).date() - timedelta(days=1)),
    )


def _active_user(*, spec=None, email: str = "user@example.com", firebase_uid: str = "firebase-uid-abc"):
    user = MagicMock(spec=spec) if spec is not None else MagicMock()
    user.id = uuid4()
    user.email = email
    user.firebase_uid = firebase_uid
    user.status = UserStatus.active
    user.is_deleted = False
    user.deleted_at = None
    user.email_verified_at = datetime.now(timezone.utc)
    user.has_changed_email_after_graduation = False
    user.email_otp = "1234"
    user.email_otp_created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    return user


def _eligible_change_email_mocks(monkeypatch, *, profile: Profile | None = None) -> None:
    profile = profile or _graduated_profile()
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=profile),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service.log_security_event",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.chat.service.revoke_stream_user_tokens_best_effort",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service.unlink_social_login_providers",
        lambda uid: [],
    )
    monkeypatch.setattr(
        "apps.profiles.services.build_user_base_response",
        AsyncMock(return_value={"email": "newemail@example.com"}),
    )


@pytest.mark.asyncio
async def test_change_email_success(monkeypatch, db_scalar_result):
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalar_result(None))
    mock_db.refresh = AsyncMock()

    firebase_updates: list[tuple[str, str]] = []
    revoked_uids: list[str] = []
    stored_refresh: list[str] = []

    monkeypatch.setattr(
        "apps.accounts.services.email_service.update_firebase_email",
        lambda uid, email: firebase_updates.append((uid, email)),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service.revoke_firebase_tokens",
        lambda uid: revoked_uids.append(uid),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service.create_firebase_custom_token",
        lambda uid: f"custom-token-for-{uid}",
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service._generate_tokens",
        lambda _user: ("new-access", "new-refresh"),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service._store_refresh_token",
        AsyncMock(side_effect=lambda db, u, token, device_id=None: stored_refresh.append(token)),
    )
    _eligible_change_email_mocks(monkeypatch)

    payload = ChangeEmailRequest(newEmail="NEWEMAIL@example.com")
    result = await change_email(user, payload, mock_db)

    assert result.status is True
    assert result.message == "Email updated successfully"
    assert user.email == "newemail@example.com"
    assert user.firebase_uid == "firebase-uid-abc"
    assert user.email_verified_at is None
    assert user.has_changed_email_after_graduation is True
    assert user.email_otp is None
    assert user.email_otp_created_at is None
    assert firebase_updates == [("firebase-uid-abc", "newemail@example.com")]
    assert revoked_uids == ["firebase-uid-abc"]
    assert stored_refresh == ["new-refresh"]
    assert result.data["access_token"] == "new-access"
    assert result.data["refresh_token"] == "new-refresh"
    assert result.data["firebaseCustomToken"] == "custom-token-for-firebase-uid-abc"
    assert result.data["needsOtp"] is True
    assert result.data["user"]["email"] == "newemail@example.com"
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_email_rejects_same_email(db_scalar_result):
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    mock_db = AsyncMock()

    payload = ChangeEmailRequest(newEmail="user@example.com")
    result = await change_email(user, payload, mock_db)

    assert result.status is False
    assert "different" in result.message.lower()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_rejects_duplicate_postgresql_email(monkeypatch, db_scalar_result):
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    other_user = _active_user(email="taken@example.com")
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalar_result(other_user))

    profile = _graduated_profile()
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=profile),
    )

    payload = ChangeEmailRequest(newEmail="taken@example.com")
    result = await change_email(user, payload, mock_db)

    assert result.status is False
    assert result.message == "Email already registered"
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_handles_firebase_email_already_exists(monkeypatch, db_scalar_result):
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    original_email = user.email
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalar_result(None))

    profile = _graduated_profile()
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=profile),
    )

    def _raise_exists(_uid, _email):
        raise firebase_auth.EmailAlreadyExistsError(
            "exists",
            Exception("cause"),
            MagicMock(),
        )

    monkeypatch.setattr(
        "apps.accounts.services.email_service.update_firebase_email",
        _raise_exists,
    )

    payload = ChangeEmailRequest(newEmail="exists@example.com")
    result = await change_email(user, payload, mock_db)

    assert result.status is False
    assert result.message == "Email already registered"
    assert user.email == original_email
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_firebase_failure_does_not_commit(monkeypatch, db_scalar_result):
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    original_email = user.email
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalar_result(None))

    profile = _graduated_profile()
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=profile),
    )

    def _raise_generic(_uid, _email):
        raise RuntimeError("firebase down")

    monkeypatch.setattr(
        "apps.accounts.services.email_service.update_firebase_email",
        _raise_generic,
    )

    payload = ChangeEmailRequest(newEmail="newemail@example.com")
    result = await change_email(user, payload, mock_db)

    assert result.status is False
    assert result.message == "Failed to update email in Firebase"
    assert user.email == original_email
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_integrity_error_reverts_firebase(monkeypatch, db_scalar_result):
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            db_scalar_result(None),
            MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(all=MagicMock(return_value=[]))
                )
            ),
        ]
    )
    mock_db.commit = AsyncMock(side_effect=IntegrityError("insert", {}, Exception("duplicate")))
    mock_db.rollback = AsyncMock()

    firebase_updates: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "apps.accounts.services.email_service.update_firebase_email",
        lambda uid, email: firebase_updates.append((uid, email)),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service.revoke_firebase_tokens",
        lambda uid: None,
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service.unlink_social_login_providers",
        lambda uid: [],
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service._generate_tokens",
        lambda _user: ("new-access", "new-refresh"),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service._store_refresh_token",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service.log_security_event",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.chat.service.revoke_stream_user_tokens_best_effort",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=_graduated_profile()),
    )

    payload = ChangeEmailRequest(newEmail="newemail@example.com")
    result = await change_email(user, payload, mock_db)

    assert result.status is False
    assert result.message == "Email already registered"
    assert firebase_updates == [
        ("firebase-uid-abc", "newemail@example.com"),
        ("firebase-uid-abc", "user@example.com"),
    ]
    mock_db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_email_revoke_failure_reverts_firebase(monkeypatch, db_scalar_result):
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    original_email = user.email
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalar_result(None))

    firebase_updates: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "apps.accounts.services.email_service.update_firebase_email",
        lambda uid, email: firebase_updates.append((uid, email)),
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service.unlink_social_login_providers",
        lambda uid: ["google.com"],
    )

    def _raise_revoke(_uid):
        raise RuntimeError("revoke failed")

    monkeypatch.setattr(
        "apps.accounts.services.email_service.revoke_firebase_tokens",
        _raise_revoke,
    )
    _eligible_change_email_mocks(monkeypatch)

    payload = ChangeEmailRequest(newEmail="newemail@example.com")
    result = await change_email(user, payload, mock_db)

    assert result.status is False
    assert "could not be updated" in result.message.lower()
    assert user.email == original_email
    assert firebase_updates == [
        ("firebase-uid-abc", "newemail@example.com"),
        ("firebase-uid-abc", "user@example.com"),
    ]
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_rejects_disposable_when_enabled(monkeypatch, db_scalar_result):
    from apps.accounts.services import email_service as email_service_module

    user = _active_user(spec=User)
    mock_db = AsyncMock()

    monkeypatch.setattr(
        email_service_module.auth_settings,
        "is_disposable_email_enabled",
        True,
    )

    def _validate(email, *, is_enabled):
        if is_enabled:
            raise ApiError("Disposable email addresses are not allowed.")

    monkeypatch.setattr(
        "common.email_validation.validate_disposable_email",
        _validate,
    )
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=_graduated_profile()),
    )

    payload = ChangeEmailRequest(newEmail="temporary@mailinator.com")
    result = await email_service_module.change_email(user, payload, mock_db)

    assert result.status is False
    assert "disposable" in result.message.lower()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_allows_disposable_when_disabled(monkeypatch, db_scalar_result):
    from apps.accounts.services import email_service as email_service_module

    user = _active_user(spec=User)
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=db_scalar_result(None))
    mock_db.refresh = AsyncMock()

    monkeypatch.setattr(
        email_service_module.auth_settings,
        "is_disposable_email_enabled",
        False,
    )
    monkeypatch.setattr(
        email_service_module,
        "update_firebase_email",
        lambda uid, email: None,
    )
    monkeypatch.setattr(
        email_service_module,
        "revoke_firebase_tokens",
        lambda uid: None,
    )
    monkeypatch.setattr(
        email_service_module,
        "create_firebase_custom_token",
        lambda uid: "custom-token",
    )
    monkeypatch.setattr(
        email_service_module,
        "_generate_tokens",
        lambda _user: ("access", "refresh"),
    )
    monkeypatch.setattr(
        email_service_module,
        "_store_refresh_token",
        AsyncMock(),
    )
    _eligible_change_email_mocks(
        monkeypatch,
        profile=_graduated_profile(),
    )
    monkeypatch.setattr(
        "apps.profiles.services.build_user_base_response",
        AsyncMock(return_value={"email": "temporary@mailinator.com"}),
    )

    payload = ChangeEmailRequest(newEmail="temporary@mailinator.com")
    result = await email_service_module.change_email(user, payload, mock_db)

    assert result.status is True
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_change_email_rejects_account_without_firebase_uid(monkeypatch, db_scalar_result):
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User, firebase_uid=None)
    mock_db = AsyncMock()

    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=_graduated_profile()),
    )

    payload = ChangeEmailRequest(newEmail="newemail@example.com")
    result = await change_email(user, payload, mock_db)

    assert result.status is False
    assert "not available" in result.message.lower()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_rejects_unverified_email(monkeypatch) -> None:
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    user.email_verified_at = None
    mock_db = AsyncMock()
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=_graduated_profile()),
    )

    result = await change_email(user, ChangeEmailRequest(newEmail="newemail@example.com"), mock_db)

    assert result.status is False
    assert "verified" in result.message.lower()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_rejects_incomplete_graduation(monkeypatch) -> None:
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    mock_db = AsyncMock()
    future_graduation = datetime.now(timezone.utc).date() + timedelta(days=30)
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=_graduated_profile(graduation_date=future_graduation)),
    )

    result = await change_email(user, ChangeEmailRequest(newEmail="newemail@example.com"), mock_db)

    assert result.status is False
    assert "graduation" in result.message.lower()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_rejects_already_changed_after_graduation(monkeypatch) -> None:
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    user.has_changed_email_after_graduation = True
    mock_db = AsyncMock()
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=_graduated_profile()),
    )

    result = await change_email(user, ChangeEmailRequest(newEmail="newemail@example.com"), mock_db)

    assert result.status is False
    assert "already changed" in result.message.lower()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_change_email_rejects_unverified_and_not_graduated(monkeypatch) -> None:
    from apps.accounts.services.email_service import change_email

    user = _active_user(spec=User)
    user.email_verified_at = None
    mock_db = AsyncMock()
    monkeypatch.setattr(
        "apps.accounts.services.email_service._fetch_user_profile",
        AsyncMock(return_value=Profile(user_id=user.id, graduation_date=None)),
    )

    result = await change_email(user, ChangeEmailRequest(newEmail="newemail@example.com"), mock_db)

    assert result.status is False
    assert "verified" in result.message.lower()
    mock_db.commit.assert_not_awaited()


def test_change_email_request_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        ChangeEmailRequest(newEmail="not-an-email")


def test_change_email_request_normalizes_email() -> None:
    payload = ChangeEmailRequest(newEmail="  User@Example.COM  ")
    assert payload.newEmail == "user@example.com"


def test_change_email_route_uses_authenticated_user(monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from entrypoints.api import app
    from core.security.auth import get_current_user
    from core.auth.dependencies import require_recent_auth
    from core.database.session import get_session

    current_user = _active_user(spec=User)
    service_calls: list[tuple] = []

    async def _override_user():
        return current_user

    async def _override_recent_auth():
        return {"uid": current_user.firebase_uid, "auth_time": 1_700_000_000}

    class _NoopSession:
        pass

    async def _override_session():
        yield _NoopSession()

    async def _mock_change_email(user, payload, db):
        service_calls.append((user, payload.newEmail))
        from apps.accounts.schemas import ApiResponse

        return ApiResponse(
            status=True,
            message="Email updated successfully",
            data={"user": {"email": payload.newEmail}},
        )

    monkeypatch.setattr(
        "apps.profiles.routes.change_email_service",
        _mock_change_email,
    )

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[require_recent_auth] = _override_recent_auth
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)
    response = client.patch(
        "/api/v1/users/me/email",
        json={"newEmail": "newemail@example.com"},
        headers={"Authorization": "Bearer firebase-id-token"},
    )

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(require_recent_auth, None)
    app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Email updated successfully"
    assert service_calls == [(current_user, "newemail@example.com")]
