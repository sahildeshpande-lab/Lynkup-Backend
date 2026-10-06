from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from apps.accounts.db_models import ConsentRecord, User
from apps.accounts.schemas import EmailSignupRequest
from apps.accounts.services import registration_service as reg_svc
from apps.accounts.services.common_service import SIGNUP_GENERIC_FAILURE_MESSAGE
from common.exceptions import ApiError
from tests.unit.conftest import FakeScalarResult


def _signup_payload(email: str) -> EmailSignupRequest:
    return EmailSignupRequest(
        firstName="Jane",
        lastName="Doe",
        email=email,
        password="Secret123",
        role="user",
        firebaseId="valid-firebase-id-token",
    )


def _signup_db(*, existing_user: User | None = None) -> Mock:
    added: list[object] = []
    lookups_remaining = 0 if existing_user is not None else 2

    async def execute(_statement):
        nonlocal lookups_remaining
        if existing_user is not None and lookups_remaining == 0:
            lookups_remaining = -1
            return FakeScalarResult(existing_user)
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
    db._added = added
    return db


@pytest.fixture
def signup_patches(monkeypatch):
    monkeypatch.setattr(reg_svc, "assign_user_role", AsyncMock())
    monkeypatch.setattr(reg_svc, "begin_otp_challenge", AsyncMock(return_value=True))
    monkeypatch.setattr(
        reg_svc,
        "_issue_auth_session",
        AsyncMock(return_value={"user": {"email": "jane@example.com"}, "emailSent": True}),
    )
    monkeypatch.setattr(
        "apps.profiles.services.calculate_completeness_score",
        AsyncMock(return_value=10),
    )


def _consent_records(db: Mock) -> list[ConsentRecord]:
    return [item for item in db._added if isinstance(item, ConsentRecord)]


@pytest.mark.asyncio
async def test_signup_creates_postgres_terms_consent(signup_patches) -> None:
    firebase_uid = f"signup-consent-{uuid4()}"
    email = f"signup_consent_{uuid4()}@example.com"
    db = _signup_db()

    result = await reg_svc.signup(
        _signup_payload(email),
        {"uid": firebase_uid, "email": email},
        db,
    )

    assert result.status is True
    assert result.message == "Signup successful"
    db.commit.assert_awaited()

    users = [item for item in db._added if isinstance(item, User)]
    assert len(users) == 1
    assert users[0].firebase_uid == firebase_uid

    consents = _consent_records(db)
    assert len(consents) == 1
    consent = consents[0]
    assert consent.user_id == users[0].id
    assert consent.consent_type == "terms_and_conditions"
    assert consent.granted is True
    assert consent.ip_address is None
    assert consent.consented_at is not None


@pytest.mark.asyncio
async def test_signup_consent_does_not_call_firestore(monkeypatch, signup_patches) -> None:
    firestore_client = Mock(side_effect=AssertionError("Firestore should not be called"))
    monkeypatch.setattr("firebase_admin.firestore.client", firestore_client)

    firebase_uid = f"signup-no-firestore-{uuid4()}"
    email = f"signup_no_firestore_{uuid4()}@example.com"
    db = _signup_db()

    result = await reg_svc.signup(
        _signup_payload(email),
        {"uid": firebase_uid, "email": email},
        db,
    )

    assert result.status is True
    firestore_client.assert_not_called()
    assert _consent_records(db)


@pytest.mark.asyncio
async def test_signup_consent_failure_does_not_commit_user(monkeypatch, signup_patches) -> None:
    monkeypatch.setattr(
        reg_svc,
        "save_current_consent",
        AsyncMock(side_effect=RuntimeError("consent insert failed")),
    )
    begin_otp = AsyncMock(return_value=True)
    monkeypatch.setattr(reg_svc, "begin_otp_challenge", begin_otp)

    firebase_uid = f"signup-consent-fail-{uuid4()}"
    email = f"signup_consent_fail_{uuid4()}@example.com"
    db = _signup_db()
    db.rollback = AsyncMock()

    with patch(
        "apps.accounts.services.registration_service.delete_firebase_user_safely",
        Mock(),
    ):
        result = await reg_svc.signup(
            _signup_payload(email),
            {"uid": firebase_uid, "email": email},
            db,
        )

    assert result.status is False
    assert result.message == SIGNUP_GENERIC_FAILURE_MESSAGE
    db.commit.assert_not_awaited()
    begin_otp.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_signup_does_not_create_consent_for_existing_user(signup_patches) -> None:
    firebase_uid = f"existing-uid-{uuid4()}"
    email = f"existing_user_{uuid4()}@example.com"
    existing = User(
        firebase_uid=firebase_uid,
        email=email,
        password_hash="hashed",
    )
    db = _signup_db(existing_user=existing)
    db.rollback = AsyncMock()

    with pytest.raises(ApiError, match="Account already exists"):
        await reg_svc.signup(
            _signup_payload(email),
            {"uid": firebase_uid, "email": email},
            db,
        )

    assert _consent_records(db) == []
    db.commit.assert_not_awaited()


def test_email_signup_request_does_not_require_consent_fields() -> None:
    payload = EmailSignupRequest(
        firstName="Jane",
        lastName="Doe",
        email="jane.consent@example.com",
        password="Secret123",
        role="user",
        firebaseId="valid-firebase-id-token",
    )
    fields = EmailSignupRequest.model_fields
    assert "terms_accepted" not in fields
    assert "privacy_accepted" not in fields
    assert "terms_version" not in fields
    assert "privacy_version" not in fields
    dumped = payload.model_dump()
    assert "terms_accepted" not in dumped
    assert "privacy_accepted" not in dumped
