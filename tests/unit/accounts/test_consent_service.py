from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from apps.accounts.db_models import ConsentRecord
from apps.accounts.services import consent_service
from apps.accounts.services.consent_service import (
    CONSENT_SOURCE_ADMIN_CREATION,
    CONSENT_SOURCE_SIGNUP,
    CONSENT_TYPE_TERMS_AND_CONDITIONS,
    save_current_consent,
)


def _consent_db(*, flush_error: Exception | None = None) -> Mock:
    added: list[object] = []

    async def flush() -> None:
        if flush_error is not None:
            raise flush_error

    db = Mock()
    db.add = added.append
    db.flush = AsyncMock(side_effect=flush)
    db.commit = AsyncMock()
    db._added = added
    return db


@pytest.mark.asyncio
async def test_save_current_consent_inserts_terms_record() -> None:
    db = _consent_db()
    user_id = uuid4()

    record = await save_current_consent(db, user_id, source=CONSENT_SOURCE_SIGNUP)

    assert len(db._added) == 1
    stored = db._added[0]
    assert stored is record
    assert isinstance(stored, ConsentRecord)
    assert stored.user_id == user_id
    assert stored.consent_type == CONSENT_TYPE_TERMS_AND_CONDITIONS
    assert stored.consent_type == "terms_and_conditions"
    assert stored.granted is True
    assert stored.ip_address is None
    assert stored.consented_at is not None
    db.flush.assert_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_current_consent_uses_same_type_for_admin_creation() -> None:
    db = _consent_db()
    user_id = uuid4()

    record = await save_current_consent(db, user_id, source=CONSENT_SOURCE_ADMIN_CREATION)

    assert record.consent_type == "terms_and_conditions"
    assert record.granted is True
    assert record.ip_address is None
    assert record.user_id == user_id


@pytest.mark.asyncio
async def test_save_current_consent_does_not_create_privacy_record() -> None:
    db = _consent_db()

    await save_current_consent(db, uuid4(), source=CONSENT_SOURCE_SIGNUP)

    types = [item.consent_type for item in db._added if isinstance(item, ConsentRecord)]
    assert types == ["terms_and_conditions"]


def test_consent_service_does_not_use_firestore() -> None:
    source = inspect.getsource(consent_service)
    assert "firestore" not in source
    assert "google.cloud" not in source
    assert "firebase_admin" not in source
    assert not hasattr(consent_service, "firestore")


@pytest.mark.asyncio
async def test_save_current_consent_requires_user_id() -> None:
    db = _consent_db()
    with pytest.raises(ValueError, match="user_id is required"):
        await save_current_consent(db, None, source=CONSENT_SOURCE_SIGNUP)
    db.flush.assert_not_awaited()
    assert db._added == []


@pytest.mark.asyncio
async def test_save_current_consent_propagates_database_errors() -> None:
    db = _consent_db(flush_error=RuntimeError("consent insert failed"))

    with pytest.raises(RuntimeError, match="consent insert failed"):
        await save_current_consent(db, uuid4(), source=CONSENT_SOURCE_SIGNUP)

    db.commit.assert_not_awaited()
