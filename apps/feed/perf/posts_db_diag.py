"""Diagnostic-only DB timing for GET /api/v1/posts.

Enabled via POSTS_DB_DIAGNOSTICS=true. Does not change query behavior or pool config.
"""

from __future__ import annotations

import contextvars
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.feed.perf.posts_perf import perf_ms

logger = logging.getLogger(__name__)

_pool_listeners_registered = False
_checkout_wait_starts: dict[int, float] = {}


def posts_db_diagnostics_enabled() -> bool:
    return os.getenv("POSTS_DB_DIAGNOSTICS", "").lower() in {"1", "true", "yes", "on"}


@dataclass
class DbTrace:
    query: str
    session_create_ms: float = 0.0
    connection_checkout_ms: float = 0.0
    sql_execute_ms: float = 0.0
    result_fetch_ms: float = 0.0
    session_close_ms: float = 0.0
    total_ms: float = 0.0
    connection_reused: bool = False
    _total_started: float = field(default=0.0, repr=False)
    _execute_requested_at: float | None = field(default=None, repr=False)
    _checkout_recorded: bool = field(default=False, repr=False)

    def mark_execute_requested(self) -> None:
        if self._checkout_recorded:
            return
        self._execute_requested_at = time.perf_counter()

    def record_checkout_from_pool(self, connection_record_id: int) -> None:
        if self._checkout_recorded or self.connection_reused:
            return
        started = _checkout_wait_starts.pop(connection_record_id, None)
        if started is None and self._execute_requested_at is not None:
            started = self._execute_requested_at
        if started is not None:
            self.connection_checkout_ms = (time.perf_counter() - started) * 1000.0
            self._checkout_recorded = True


_active_trace: contextvars.ContextVar[DbTrace | None] = contextvars.ContextVar(
    "posts_db_active_trace",
    default=None,
)


def get_active_db_trace() -> DbTrace | None:
    return _active_trace.get()


def begin_request_db_trace(query: str) -> DbTrace | None:
    """Start a trace for an operation on the request-scoped session (connection usually reused)."""
    if not posts_db_diagnostics_enabled():
        return None
    register_pool_diagnostic_listeners()
    trace = DbTrace(
        query=query,
        connection_reused=True,
        _total_started=time.perf_counter(),
    )
    _active_trace.set(trace)
    return trace


def finish_request_db_trace(trace: DbTrace | None) -> None:
    if trace is None:
        return
    trace.total_ms = perf_ms(trace._total_started)
    _active_trace.set(None)
    log_db_trace(trace)


def log_db_trace(trace: DbTrace) -> None:
    if not posts_db_diagnostics_enabled():
        return
    reused = " connection_reused=true" if trace.connection_reused else ""
    logger.info(
        "[POSTS DB TRACE] query=%s session_create_ms=%.2f "
        "connection_checkout_ms=%.2f sql_execute_ms=%.2f result_fetch_ms=%.2f "
        "session_close_ms=%.2f total_ms=%.2f%s",
        trace.query,
        trace.session_create_ms,
        trace.connection_checkout_ms,
        trace.sql_execute_ms,
        trace.result_fetch_ms,
        trace.session_close_ms,
        trace.total_ms,
        reused,
    )


def log_pool_status(phase: str) -> None:
    if not posts_db_diagnostics_enabled():
        return
    try:
        from core.database.config import settings
        from core.database.session import engine

        pool = engine.sync_engine.pool
        pool_size = settings.db_pool_size
        checked_in = pool.checkedin()
        checked_out = pool.checkedout()
        overflow = pool.overflow()
    except Exception as exc:  # pragma: no cover - diagnostic only
        logger.info(
            "[POSTS POOL] phase=%s status=unavailable error=%s",
            phase,
            type(exc).__name__,
        )
        return

    logger.info(
        "[POSTS POOL] phase=%s pool_size=%s checked_in=%s checked_out=%s overflow=%s",
        phase,
        pool_size,
        checked_in,
        checked_out,
        overflow,
    )


def register_pool_diagnostic_listeners() -> None:
    """Attach read-only pool/cursor listeners to measure checkout and SQL time."""
    global _pool_listeners_registered
    if _pool_listeners_registered or not posts_db_diagnostics_enabled():
        return

    from core.database.session import engine

    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "checkout")
    def _on_checkout(
        dbapi_connection: Any,
        connection_record: Any,
        connection_proxy: Any,
    ) -> None:
        trace = get_active_db_trace()
        if trace is None or trace.connection_reused:
            return
        _checkout_wait_starts[id(connection_record)] = time.perf_counter()
        if trace._execute_requested_at is not None:
            _checkout_wait_starts[id(connection_record)] = trace._execute_requested_at

    @event.listens_for(sync_engine, "checkin")
    def _on_checkin(dbapi_connection: Any, connection_record: Any) -> None:
        _checkout_wait_starts.pop(id(connection_record), None)

    @event.listens_for(sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:
        trace = get_active_db_trace()
        if trace is None or trace.connection_reused:
            return
        logger.info(
            "[POSTS DB TRACE] query=%s pool_event=connect new_physical_connection=true",
            trace.query,
        )

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before_cursor(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        trace = get_active_db_trace()
        if trace is None:
            return
        record_id = id(getattr(conn, "connection_record", conn))
        trace.record_checkout_from_pool(record_id)
        context._posts_db_sql_started = time.perf_counter()

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after_cursor(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        trace = get_active_db_trace()
        if trace is None:
            return
        started = getattr(context, "_posts_db_sql_started", None)
        if started is not None:
            trace.sql_execute_ms += perf_ms(started)

    _pool_listeners_registered = True


@asynccontextmanager
async def traced_parallel_session(query: str) -> AsyncIterator[AsyncSession]:
    """Own AsyncSession for parallel loaders with lifecycle timing."""
    from core.database.session import async_session_factory

    if not posts_db_diagnostics_enabled():
        async with async_session_factory() as session:
            yield session
        return

    register_pool_diagnostic_listeners()
    total_started = time.perf_counter()
    session_create_started = time.perf_counter()
    trace = DbTrace(query=query, _total_started=total_started)
    token = _active_trace.set(trace)
    session: AsyncSession | None = None
    try:
        async with async_session_factory() as session:
            trace.session_create_ms = perf_ms(session_create_started)
            yield session
    finally:
        close_started = time.perf_counter()
        _active_trace.reset(token)
        trace.session_close_ms = perf_ms(close_started)
        trace.total_ms = perf_ms(total_started)
        log_db_trace(trace)


async def timed_execute(
    db: AsyncSession,
    statement: Any,
    params: dict[str, Any] | None = None,
) -> Any:
    """Execute a statement; split execute vs fetch is caller responsibility."""
    trace = get_active_db_trace()
    if trace is not None:
        trace.mark_execute_requested()
    execute_started = time.perf_counter()
    if params is None:
        result = await db.execute(statement)
    else:
        result = await db.execute(statement, params)
    if trace is not None and trace.sql_execute_ms == 0.0:
        # Fallback when cursor events are unavailable (e.g. NullPool edge cases).
        trace.sql_execute_ms = perf_ms(execute_started)
    return result


def timed_fetch(result: Any, *, method: str = "all") -> Any:
    trace = get_active_db_trace()
    fetch_started = time.perf_counter()
    if method == "all":
        data = result.all()
    elif method == "scalar_one":
        data = result.scalar_one()
    elif method == "scalar_one_or_none":
        data = result.scalar_one_or_none()
    elif method == "mappings_all":
        data = result.mappings().all()
    elif method == "mappings_one":
        data = result.mappings().one()
    elif method == "scalars_all":
        data = result.scalars().all()
    else:
        raise ValueError(f"unsupported fetch method: {method}")
    if trace is not None:
        trace.result_fetch_ms += perf_ms(fetch_started)
    return data


async def run_select1_probe() -> None:
    """One-shot SELECT 1 probe using the app engine (POSTS_DB_DIAGNOSTICS only)."""
    if not posts_db_diagnostics_enabled():
        return

    from core.database.session import async_session_factory

    register_pool_diagnostic_listeners()
    total_started = time.perf_counter()
    connect_ms = 0.0
    checkout_ms = 0.0
    execute_ms = 0.0
    fetch_ms = 0.0

    session_create_started = time.perf_counter()
    trace = DbTrace(query="select1_probe", _total_started=total_started)
    token = _active_trace.set(trace)
    try:
        async with async_session_factory() as session:
            connect_ms = perf_ms(session_create_started)
            trace.mark_execute_requested()
            execute_started = time.perf_counter()
            result = await session.execute(text("SELECT 1"))
            execute_ms = perf_ms(execute_started)
            if trace.sql_execute_ms > 0:
                execute_ms = trace.sql_execute_ms
            checkout_ms = trace.connection_checkout_ms
            fetch_started = time.perf_counter()
            result.scalar_one()
            fetch_ms = perf_ms(fetch_started)
    finally:
        _active_trace.reset(token)

    total_ms = perf_ms(total_started)
    logger.info(
        "[POSTS DB PROBE] connect_ms=%.2f checkout_ms=%.2f execute_ms=%.2f "
        "fetch_ms=%.2f total_ms=%.2f",
        connect_ms,
        checkout_ms,
        execute_ms,
        fetch_ms,
        total_ms,
    )
