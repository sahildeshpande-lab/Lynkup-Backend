from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update

from apps.export.claims import claim_export


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


def _statement(db):
    return db.execute.await_args.args[0]


def _claim_sql(db) -> str:
    return _compile(_statement(db))


@pytest.fixture
def export_id():
    return uuid4()


@pytest.mark.asyncio
async def test_queued_export_is_successfully_claimed(mock_db, export_id):
    db = mock_db(SimpleNamespace(rowcount=1))

    claimed = await claim_export(db, export_id, "worker-1", lease_seconds=1800)

    assert claimed is True
    assert db.execute.await_count == 1
    stmt = _statement(db)
    assert isinstance(stmt, Update)
    sql = _compile(stmt)
    assert "update data_export_requests" in sql
    assert "queued" in sql
    assert "processing" in sql
    assert "lease_owner" in sql
    assert "attempt_count" in sql
    assert "coalesce" in sql
    assert "now()" in sql
    assert "interval '1800 seconds'" in sql


@pytest.mark.asyncio
async def test_processing_with_active_lease_is_not_claimed(mock_db, export_id):
    db = mock_db(SimpleNamespace(rowcount=0))

    claimed = await claim_export(db, export_id, "worker-2")

    assert claimed is False
    sql = _claim_sql(db)
    where_sql = sql.split("where", 1)[-1]
    assert "lease_expires_at" in where_sql
    assert "now()" in where_sql
    assert "queued" in where_sql
    assert "processing" in where_sql
    assert "completed" not in where_sql
    assert "failed" not in where_sql


@pytest.mark.asyncio
async def test_processing_with_expired_lease_is_reclaimed(mock_db, export_id):
    db = mock_db(SimpleNamespace(rowcount=1))

    claimed = await claim_export(db, export_id, "worker-3", lease_seconds=900)

    assert claimed is True
    sql = _claim_sql(db)
    assert "processing" in sql
    assert "lease_expires_at" in sql
    assert "now()" in sql
    assert "interval '900 seconds'" in sql
    assert "attempt_count" in sql
    assert "+ 1" in sql or "+1" in sql


@pytest.mark.asyncio
async def test_completed_export_is_not_claimed(mock_db, export_id):
    db = mock_db(SimpleNamespace(rowcount=0))

    claimed = await claim_export(db, export_id, "worker-4")

    assert claimed is False
    sql = _claim_sql(db)
    where_sql = sql.split("where", 1)[-1]
    assert "completed" not in where_sql
    assert "queued" in where_sql
    assert "processing" in where_sql


@pytest.mark.asyncio
async def test_failed_export_is_not_claimed(mock_db, export_id):
    db = mock_db(SimpleNamespace(rowcount=0))

    claimed = await claim_export(db, export_id, "worker-5")

    assert claimed is False
    sql = _claim_sql(db)
    where_sql = sql.split("where", 1)[-1]
    assert "failed" not in where_sql
    assert "queued" in where_sql
    assert "processing" in where_sql


@pytest.mark.asyncio
async def test_two_concurrent_claim_attempts_only_one_succeeds(mock_db, export_id):
    db = mock_db(SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=0))

    results = await asyncio.gather(
        claim_export(db, export_id, "worker-a"),
        claim_export(db, export_id, "worker-b"),
    )

    assert results.count(True) == 1
    assert results.count(False) == 1
    assert db.execute.await_count == 2
    for call in db.execute.await_args_list:
        assert isinstance(call.args[0], Update)
