from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update

from core.jobs.claims import claim_transactional_email


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


def _statement(db):
    return db.execute.await_args.args[0]


@pytest.fixture
def email_id():
    return uuid4()


@pytest.mark.asyncio
async def test_pending_transactional_email_is_claimed(mock_db, email_id):
    db = mock_db(SimpleNamespace(rowcount=1))

    result = await claim_transactional_email(
        db,
        email_id=email_id,
        lease_owner="worker-1",
        lease_seconds=300,
    )

    assert result.claimed is True
    assert result.entity_id == email_id
    assert db.execute.await_count == 1
    assert db.commit.await_count == 1
    stmt = _statement(db)
    assert isinstance(stmt, Update)
    sql = _compile(stmt)
    assert "update transactional_email_log" in sql
    assert "is_sent" in sql or "is_send" in sql
    assert "lease_owner" in sql
    assert "lease_expires_at" in sql
    assert "attempt_count" in sql
    assert "+ 1" in sql or "+1" in sql


@pytest.mark.asyncio
async def test_duplicate_execution_cannot_claim_the_same_email(mock_db, email_id):
    db = mock_db(SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=0))

    results = await asyncio.gather(
        claim_transactional_email(db, email_id=email_id, lease_owner="worker-a"),
        claim_transactional_email(db, email_id=email_id, lease_owner="worker-b"),
    )

    assert [item.claimed for item in results].count(True) == 1
    assert [item.claimed for item in results].count(False) == 1
    assert db.execute.await_count == 2
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed(mock_db, email_id):
    db = mock_db(SimpleNamespace(rowcount=1))

    result = await claim_transactional_email(
        db,
        email_id=email_id,
        lease_owner="worker-3",
        lease_seconds=120,
    )

    assert result.claimed is True
    sql = _compile(_statement(db))
    assert "lease_expires_at" in sql
    assert "now()" in sql
    assert "attempt_count" in sql


@pytest.mark.asyncio
async def test_completed_email_is_not_claimed(mock_db, email_id):
    db = mock_db(SimpleNamespace(rowcount=0))

    result = await claim_transactional_email(
        db,
        email_id=email_id,
        lease_owner="worker-4",
    )

    assert result.claimed is False
    assert db.commit.await_count == 0
    sql = _compile(_statement(db))
    where_sql = sql.split("where", 1)[-1]
    assert "false" in where_sql
    assert "lease_expires_at" in where_sql


@pytest.mark.asyncio
async def test_active_lease_is_not_claimed(mock_db, email_id):
    db = mock_db(SimpleNamespace(rowcount=0))

    result = await claim_transactional_email(
        db,
        email_id=email_id,
        lease_owner="worker-5",
    )

    assert result.claimed is False
    sql = _compile(_statement(db))
    assert "lease_owner" in sql
    assert "lease_expires_at" in sql
    assert "now()" in sql
