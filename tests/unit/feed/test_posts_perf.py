from __future__ import annotations

import logging
import uuid
from unittest.mock import patch

import pytest

from apps.feed.perf.posts_perf import (
    db_round_trips,
    log_db_execute,
    log_metric,
    log_stage,
    log_summary_context,
    perf_ms,
    posts_perf_context,
    posts_perf_enabled,
)


def test_posts_perf_disabled_outside_context() -> None:
    assert posts_perf_enabled() is False
    assert db_round_trips() == 0


def test_perf_ms_returns_non_negative() -> None:
    import time

    started = time.perf_counter()
    assert perf_ms(started) >= 0.0


def test_posts_perf_context_enables_logging_and_round_trips(caplog) -> None:
    caplog.set_level(logging.INFO)
    with posts_perf_context():
        assert posts_perf_enabled() is True
        log_stage("timeline_fetch", 12.5, event_count=3)
        log_metric("db_round_trips", 4)
        log_db_execute("user_exists", 1.2)
        log_summary_context(
            user_id=uuid.uuid4(),
            target_user_id=None,
            state="published",
            page=1,
            page_size=20,
            item_count=3,
            total_items=0,
        )

    assert posts_perf_enabled() is False
    assert "[POSTS PERF] timeline_fetch=12.50ms event_count=3" in caplog.text
    assert "[POSTS PERF] db_round_trips=4" in caplog.text
    assert "[POSTS PERF] query_name=user_exists db_execute=1.20ms" in caplog.text
    assert "[POSTS PERF] summary user_id=" in caplog.text


def test_posts_perf_logging_noops_when_disabled(caplog) -> None:
    caplog.set_level(logging.INFO)
    log_stage("timeline_fetch", 99.0)
    log_metric("db_round_trips", 9)
    log_db_execute("count", 9.0)
    log_summary_context(
        user_id=uuid.uuid4(),
        target_user_id=None,
        state=None,
        page=None,
        page_size=None,
        item_count=0,
        total_items=0,
    )
    assert "[POSTS PERF]" not in caplog.text


def test_register_round_trip_listener_is_idempotent() -> None:
    from apps.feed.perf.posts_perf import _register_round_trip_listener

    _register_round_trip_listener()
    _register_round_trip_listener()
