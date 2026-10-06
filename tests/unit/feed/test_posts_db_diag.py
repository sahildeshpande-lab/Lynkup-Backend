from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.feed.perf.posts_db_diag import (
    DbTrace,
    begin_request_db_trace,
    finish_request_db_trace,
    log_db_trace,
    posts_db_diagnostics_enabled,
    timed_execute,
    timed_fetch,
)


def test_posts_db_diagnostics_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("POSTS_DB_DIAGNOSTICS", raising=False)
    assert posts_db_diagnostics_enabled() is False


def test_posts_db_diagnostics_enabled_true(monkeypatch) -> None:
    monkeypatch.setenv("POSTS_DB_DIAGNOSTICS", "true")
    assert posts_db_diagnostics_enabled() is True


def test_log_db_trace_format(caplog) -> None:
    caplog.set_level(logging.INFO)
    trace = DbTrace(
        query="user_exists",
        session_create_ms=1.0,
        connection_checkout_ms=0.0,
        sql_execute_ms=12.5,
        result_fetch_ms=2.0,
        session_close_ms=0.0,
        total_ms=15.5,
        connection_reused=True,
    )
    with patch(
        "apps.feed.perf.posts_db_diag.posts_db_diagnostics_enabled",
        return_value=True,
    ):
        log_db_trace(trace)

    assert "[POSTS DB TRACE] query=user_exists" in caplog.text
    assert "connection_checkout_ms=0.00" in caplog.text
    assert "sql_execute_ms=12.50" in caplog.text
    assert "connection_reused=true" in caplog.text


def test_begin_and_finish_request_db_trace(monkeypatch) -> None:
    monkeypatch.setenv("POSTS_DB_DIAGNOSTICS", "true")
    trace = begin_request_db_trace("count_user_posts_timeline")
    assert trace is not None
    assert trace.connection_reused is True
    trace.sql_execute_ms = 42.0
    trace.result_fetch_ms = 3.0
    finish_request_db_trace(trace)


def test_timed_fetch_records_result_fetch_ms(monkeypatch) -> None:
    monkeypatch.setenv("POSTS_DB_DIAGNOSTICS", "true")
    trace = begin_request_db_trace("fetch_user_posts_timeline_events")
    assert trace is not None
    result = MagicMock()
    result.all.return_value = [("a", "b", "c", "d")]
    rows = timed_fetch(result, method="all")
    assert rows == [("a", "b", "c", "d")]
    assert trace.result_fetch_ms >= 0.0
    finish_request_db_trace(trace)


@pytest.mark.asyncio
async def test_traced_parallel_session_logs_on_exit(monkeypatch, caplog) -> None:
    monkeypatch.setenv("POSTS_DB_DIAGNOSTICS", "true")
    caplog.set_level(logging.INFO)

    session = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None

    with patch(
        "core.database.session.async_session_factory",
        return_value=session_cm,
    ):
        from apps.feed.perf.posts_db_diag import traced_parallel_session

        async with traced_parallel_session("fetch_post_engagement_flags") as db:
            assert db is session

    assert "[POSTS DB TRACE] query=fetch_post_engagement_flags" in caplog.text


def test_db_trace_checkout_helpers() -> None:
    trace = DbTrace(query="hydrate_posts_main")
    trace.mark_execute_requested()
    trace.record_checkout_from_pool(42)
    assert trace.connection_checkout_ms >= 0.0

    trace2 = DbTrace(query="parallel", connection_reused=False)
    trace2.mark_execute_requested()
    trace2.record_checkout_from_pool(99)
    assert trace2._checkout_recorded is True


def test_log_db_trace_skips_when_disabled(monkeypatch, caplog) -> None:
    monkeypatch.delenv("POSTS_DB_DIAGNOSTICS", raising=False)
    caplog.set_level(logging.INFO)
    trace = DbTrace(query="noop", total_ms=1.0)
    log_db_trace(trace)
    assert "[POSTS DB TRACE]" not in caplog.text


def test_log_pool_status_logs_when_enabled(monkeypatch, caplog) -> None:
    monkeypatch.setenv("POSTS_DB_DIAGNOSTICS", "true")
    caplog.set_level(logging.INFO)
    from apps.feed.perf.posts_db_diag import log_pool_status

    log_pool_status("after_timeline")
    assert "[POSTS POOL] phase=after_timeline" in caplog.text


def test_timed_fetch_all_methods(monkeypatch) -> None:
    monkeypatch.setenv("POSTS_DB_DIAGNOSTICS", "true")
    trace = begin_request_db_trace("fetch")
    assert trace is not None

    result = MagicMock()
    result.all.return_value = [1]
    result.scalar_one.return_value = 2
    result.scalar_one_or_none.return_value = 3
    result.mappings.return_value.all.return_value = [{"id": 4}]
    result.mappings.return_value.one.return_value = {"id": 5}
    result.scalars.return_value.all.return_value = [6]

    assert timed_fetch(result, method="all") == [1]
    assert timed_fetch(result, method="scalar_one") == 2
    assert timed_fetch(result, method="scalar_one_or_none") == 3
    assert timed_fetch(result, method="mappings_all") == [{"id": 4}]
    assert timed_fetch(result, method="mappings_one") == {"id": 5}
    assert timed_fetch(result, method="scalars_all") == [6]

    with pytest.raises(ValueError, match="unsupported fetch method"):
        timed_fetch(result, method="bad")

    finish_request_db_trace(trace)


@pytest.mark.asyncio
async def test_timed_execute_records_sql_ms(monkeypatch) -> None:
    monkeypatch.setenv("POSTS_DB_DIAGNOSTICS", "true")
    trace = begin_request_db_trace("execute")
    assert trace is not None

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    await timed_execute(db, "SELECT 1")
    await timed_execute(db, "SELECT 2", {"x": 1})
    assert trace.sql_execute_ms >= 0.0
    finish_request_db_trace(trace)


@pytest.mark.asyncio
async def test_traced_parallel_session_disabled_short_circuits(monkeypatch) -> None:
    monkeypatch.delenv("POSTS_DB_DIAGNOSTICS", raising=False)
    session = AsyncMock()
    session_cm = AsyncMock()
    session_cm.__aenter__.return_value = session
    session_cm.__aexit__.return_value = None

    with patch(
        "core.database.session.async_session_factory",
        return_value=session_cm,
    ):
        from apps.feed.perf.posts_db_diag import traced_parallel_session

        async with traced_parallel_session("noop") as db:
            assert db is session
