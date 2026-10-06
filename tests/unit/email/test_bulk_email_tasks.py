from __future__ import annotations

import asyncio
import inspect

from core.celery_worker.config import CeleryTaskQueue

import pytest

from apps.bulk_send.cron import process_bulk_emails
from core.celery_worker.celery_app import celery_app
from core.email_tasks import bulk_email_tick, transactional_email_tick


def _queue_name(task) -> str:
    queue = getattr(task, "queue", None)
    if queue is None:
        queue = (task._get_exec_options() or {}).get("queue")
    return queue


def _schedule_seconds(entry) -> float:
    schedule = entry["schedule"]
    seconds = getattr(schedule, "run_every", schedule)
    if hasattr(seconds, "total_seconds"):
        seconds = seconds.total_seconds()
    return float(seconds)


def test_bulk_task_name_and_bulk_email_queue():
    assert bulk_email_tick.name == "kampulynk.email.bulk.tick"
    assert _queue_name(bulk_email_tick) == CeleryTaskQueue.BULK_EMAIL_QUEUE.value
    assert bulk_email_tick.ignore_result is True
    assert "core.email_tasks" in celery_app.conf.imports


def test_bulk_task_has_no_payload():
    params = list(inspect.signature(bulk_email_tick.run).parameters)
    forbidden = {
        "session",
        "db",
        "orm",
        "html_body",
        "content",
        "recipients",
        "email",
        "api_key",
        "credentials",
    }
    assert forbidden.isdisjoint(set(params))
    assert params == []


def test_bulk_task_module_uses_worker_runtime():
    import core.email_tasks as tasks_mod

    source = inspect.getsource(tasks_mod)
    assert "create_worker_runtime" in source
    assert "async_session_factory" not in source
    assert "asyncio.run" not in source
    assert "process_bulk_emails" in source


def test_bulk_beat_schedule_is_every_60_seconds_without_changing_transactional():
    bulk = celery_app.conf.beat_schedule["kampulynk.email.bulk.tick"]
    transactional = celery_app.conf.beat_schedule["kampulynk.email.transactional.tick"]
    assert bulk["task"] == "kampulynk.email.bulk.tick"
    assert _schedule_seconds(bulk) == 60.0
    assert bulk["options"]["queue"] == CeleryTaskQueue.BULK_EMAIL_QUEUE.value
    assert transactional["task"] == "kampulynk.email.transactional.tick"
    assert _schedule_seconds(transactional) == 60.0
    assert transactional["options"]["queue"] == CeleryTaskQueue.TRANSACTIONAL_QUEUE.value
    assert _queue_name(transactional_email_tick) == CeleryTaskQueue.TRANSACTIONAL_QUEUE.value
    assert _queue_name(bulk_email_tick) != _queue_name(transactional_email_tick)


def test_bulk_task_not_eager_only():
    assert celery_app.conf.task_always_eager in (False, None)
    assert celery_app.conf.task_ignore_result is True


def test_bulk_task_executes_existing_processor(monkeypatch):
    seen: dict = {}

    async def fake_process(*, session_factory):
        seen["session_factory"] = session_factory
        seen["called"] = True

    class Runtime:
        session_factory = object()

        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr("core.email_tasks.create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr("apps.bulk_send.cron.process_bulk_emails", fake_process)

    bulk_email_tick.run()

    assert seen["called"] is True
    assert seen["session_factory"] is Runtime.session_factory
    assert seen["closed"] is True


def test_bulk_task_level_failure_propagates(monkeypatch):
    async def boom(*, session_factory):
        raise RuntimeError("bulk email infrastructure failure")

    class Runtime:
        session_factory = object()

        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            pass

    monkeypatch.setattr("core.email_tasks.create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr("apps.bulk_send.cron.process_bulk_emails", boom)

    with pytest.raises(RuntimeError, match="infrastructure failure"):
        bulk_email_tick.run()


@pytest.mark.asyncio
async def test_process_bulk_emails_reraises_tick_failure(monkeypatch):
    async def boom(limit=None, **kwargs):
        raise RuntimeError("processor exploded")

    monkeypatch.setattr(
        "apps.bulk_send.cron.process_pending_bulk_deliveries",
        boom,
    )
    with pytest.raises(RuntimeError, match="processor exploded"):
        await process_bulk_emails()
