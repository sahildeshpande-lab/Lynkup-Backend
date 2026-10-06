from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from apps.accounts.db_models import ConsentRecord, User
from apps.administration.schemas import AdminUserCreateRequest
from apps.administration.services import user_management_service as ums
from tests.unit.conftest import FakeScalarResult


def _admin_db() -> Mock:
    added: list[object] = []
    first_lookup = True

    async def execute(_statement):
        nonlocal first_lookup
        if first_lookup:
            first_lookup = False
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
    db._added = added
    return db


@pytest.fixture
def admin_create_patches(monkeypatch):
    firebase_uid = f"firebase-consent-user-{uuid4()}"

    class _FirebaseUser:
        uid = firebase_uid

    monkeypatch.setattr(
        "apps.administration.services.user_management_service.create_firebase_user",
        lambda **_kwargs: _FirebaseUser(),
    )
    monkeypatch.setattr(
        "apps.administration.services.user_management_service.delete_firebase_user",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("apps.accounts.services.assign_user_role", AsyncMock())
    monkeypatch.setattr(
        "apps.profiles.services.calculate_completeness_score",
        AsyncMock(return_value=10),
    )
    monkeypatch.setattr(
        "apps.administration.services.user_management_service.build_user_base_response",
        AsyncMock(return_value={"email": "user@example.com", "role": "user"}),
    )
    monkeypatch.setattr(
        "core.email_service.send_temporary_password_email",
        AsyncMock(return_value=True),
    )
    return firebase_uid


@pytest.mark.asyncio
async def test_admin_create_user_writes_postgres_consent(admin_create_patches) -> None:
    email = f"admin_consent_{uuid4()}@example.com"
    payload = AdminUserCreateRequest(
        firstName="Consent",
        lastName="User",
        email=email,
        role="user",
    )
    db = _admin_db()

    result = await ums.admin_create_user(payload, db)

    assert result.status is True
    assert result.message == "User created successfully"
    db.commit.assert_awaited()

    users = [item for item in db._added if isinstance(item, User)]
    consents = [item for item in db._added if isinstance(item, ConsentRecord)]
    assert len(users) == 1
    assert len(consents) == 1
    assert consents[0].user_id == users[0].id
    assert consents[0].consent_type == "terms_and_conditions"
    assert consents[0].granted is True
    assert consents[0].ip_address is None
    assert consents[0].consented_at is not None


@pytest.mark.asyncio
async def test_admin_create_user_consent_failure_rolls_back(
    monkeypatch,
    admin_create_patches,
) -> None:
    monkeypatch.setattr(
        "apps.administration.services.user_management_service.save_current_consent",
        AsyncMock(side_effect=RuntimeError("consent insert failed")),
    )
    delete_firebase = Mock()
    monkeypatch.setattr(
        "apps.administration.services.user_management_service.delete_firebase_user",
        delete_firebase,
    )
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr("core.email_service.send_temporary_password_email", send_email)

    firebase_uid = admin_create_patches
    email = f"admin_consent_fail_{uuid4()}@example.com"
    payload = AdminUserCreateRequest(
        firstName="Consent",
        lastName="Fail",
        email=email,
        role="user",
    )
    db = _admin_db()

    with pytest.raises(RuntimeError, match="consent insert failed"):
        await ums.admin_create_user(payload, db)

    db.commit.assert_not_awaited()
    db.rollback.assert_awaited()
    delete_firebase.assert_called_once_with(firebase_uid)
    send_email.assert_not_awaited()
