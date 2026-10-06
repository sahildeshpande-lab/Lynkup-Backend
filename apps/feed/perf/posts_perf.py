"""Performance instrumentation for GET /api/v1/posts."""

from __future__ import annotations

import contextvars
import logging
import time
from contextlib import contextmanager
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_posts_perf_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "posts_perf_active",
    default=False,
)
_posts_db_round_trips: contextvars.ContextVar[int] = contextvars.ContextVar(
    "posts_db_round_trips",
    default=0,
)
_round_trip_listener_registered = False


def posts_perf_enabled() -> bool:
    return _posts_perf_active.get()


@contextmanager
def posts_perf_context():
    _register_round_trip_listener()
    token = _posts_perf_active.set(True)
    round_trip_token = _posts_db_round_trips.set(0)
    try:
        yield
    finally:
        _posts_db_round_trips.reset(round_trip_token)
        _posts_perf_active.reset(token)


def perf_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


def db_round_trips() -> int:
    """Return SQL statements completed by the current GET /posts request."""
    return _posts_db_round_trips.get()


def _register_round_trip_listener() -> None:
    """Count completed SQL statements without changing request/session behaviour."""
    global _round_trip_listener_registered
    if _round_trip_listener_registered:
        return

    from sqlalchemy import event

    from core.database.session import engine

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _count_post_request_round_trip(*_args: Any, **_kwargs: Any) -> None:
        if posts_perf_enabled():
            _posts_db_round_trips.set(_posts_db_round_trips.get() + 1)

    _round_trip_listener_registered = True


def log_stage(stage: str, elapsed_ms: float, **extra: Any) -> None:
    if not posts_perf_enabled():
        return
    if extra:
        suffix = " " + " ".join(f"{key}={value}" for key, value in extra.items())
        logger.info("[POSTS PERF] %s=%.2fms%s", stage, elapsed_ms, suffix)
    else:
        logger.info("[POSTS PERF] %s=%.2fms", stage, elapsed_ms)


def log_metric(name: str, value: Any, **extra: Any) -> None:
    """Log a non-duration request metric (for example DB round trips)."""
    if not posts_perf_enabled():
        return
    suffix = " " + " ".join(f"{key}={item}" for key, item in extra.items()) if extra else ""
    logger.info("[POSTS PERF] %s=%s%s", name, value, suffix)


def log_db_execute(query_name: str, elapsed_ms: float) -> None:
    if not posts_perf_enabled():
        return
    logger.info(
        "[POSTS PERF] query_name=%s db_execute=%.2fms",
        query_name,
        elapsed_ms,
    )


def log_summary_context(
    *,
    user_id: UUID,
    target_user_id: UUID | None,
    state: str | None,
    page: int | None,
    page_size: int | None,
    item_count: int,
    total_items: int,
) -> None:
    if not posts_perf_enabled():
        return
    logger.info(
        "[POSTS PERF] summary user_id=%s target_user_id=%s state=%s page=%s "
        "pageSize=%s item_count=%s total_items=%s",
        user_id,
        target_user_id,
        state if state is not None else "all",
        page,
        page_size,
        item_count,
        total_items,
    )
