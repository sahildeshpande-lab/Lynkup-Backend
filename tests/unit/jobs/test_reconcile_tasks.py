from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.celery_worker.config import CeleryTaskQueue

import pytest
from celery.schedules import crontab

from core.celery_worker.celery_app import celery_app
from core.jobs.reconcile_tasks import reconcile_tick
from core.jobs.reconciliation_service import ReconciliationOutcome


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


def test_reconcile_tick_name_and_background_queue():
    assert reconcile_tick.name == "kampulynk.reconcile.tick"
    assert "kampulynk.reconcile.tick" in celery_app.tasks
    assert _queue_name(reconcile_tick) == CeleryTaskQueue.BACKGROUND_QUEUE.value
    assert reconcile_tick.ignore_result is True
    assert "core.jobs.reconcile_tasks" in celery_app.conf.imports


def test_beat_schedule_is_every_60_seconds_utc_on_background():
    entry = celery_app.conf.beat_schedule["kampulynk.reconcile.tick"]
    assert entry["task"] == "kampulynk.reconcile.tick"
    assert _schedule_seconds(entry) == 60.0
    assert entry["options"]["queue"] == CeleryTaskQueue.BACKGROUND_QUEUE.value
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True
    assert not isinstance(entry["schedule"], crontab)


def test_task_module_uses_worker_owned_runtime_not_api_session():
    import core.jobs.reconcile_tasks as tasks_mod

    source = inspect.getsource(tasks_mod)
    assert "create_worker_runtime" in source
    assert "async_session_factory" not in source
    assert "asyncio.run" not in source
    params = list(inspect.signature(reconcile_tick.run).parameters)
    forbidden = {"session", "db", "orm", "credentials", "payload", "html_body"}
    assert forbidden.isdisjoint(set(params))
    assert params == []


def test_default_reconciliation_settings():
    from core.jobs.config import ReconciliationSettings

    settings = ReconciliationSettings(_env_file=None)
    assert settings.enabled is True
    assert settings.interval_seconds == 60
    assert settings.batch_size == 25


def test_disabled_reconciliation_performs_no_work(monkeypatch):
    import core.jobs.reconcile_tasks as tasks_mod

    seen = {"runtime": False, "run": False}

    monkeypatch.setattr(
        tasks_mod,
        "reconciliation_settings",
        SimpleNamespace(enabled=False, interval_seconds=60, batch_size=25),
    )
    monkeypatch.setattr(
        tasks_mod,
        "create_worker_runtime",
        lambda: seen.__setitem__("runtime", True)
        or (_ for _ in ()).throw(AssertionError("runtime")),
    )

    async def fake_run(*args, **kwargs):
        seen["run"] = True
        return ReconciliationOutcome()

    monkeypatch.setattr(tasks_mod, "_run_reconcile_tick", fake_run)

    reconcile_tick.run()

    assert seen["runtime"] is False
    assert seen["run"] is False


def test_successful_task_logs_structured_outcome(monkeypatch):
    import core.jobs.reconcile_tasks as tasks_mod

    seen: dict = {}
    outcome = ReconciliationOutcome(scanned=2, claimed=2, republished=2)

    async def fake_run(runtime):
        seen["runtime"] = runtime
        return outcome

    class Runtime:
        engine = object()
        session_factory = object()

        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(
        tasks_mod,
        "reconciliation_settings",
        SimpleNamespace(enabled=True, interval_seconds=60, batch_size=25),
    )
    monkeypatch.setattr(tasks_mod, "create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr(tasks_mod, "_run_reconcile_tick", fake_run)

    reconcile_tick.run()

    assert seen["runtime"] is not None
    assert seen["closed"] is True


def test_unexpected_service_failure_propagates(monkeypatch):
    import core.jobs.reconcile_tasks as tasks_mod

    async def boom(runtime):
        raise RuntimeError("reconciliation infrastructure failure")

    class Runtime:
        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()
        close = MagicMock()

    monkeypatch.setattr(
        tasks_mod,
        "reconciliation_settings",
        SimpleNamespace(enabled=True, interval_seconds=60, batch_size=25),
    )
    monkeypatch.setattr(tasks_mod, "create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr(tasks_mod, "_run_reconcile_tick", boom)

    with pytest.raises(RuntimeError, match="infrastructure failure"):
        reconcile_tick.run()
    Runtime.close.assert_called_once()


def test_task_does_not_duplicate_business_workflows():
    import core.jobs.reconcile_tasks as tasks_mod
    import core.jobs.reconciliation_service as service_mod

    task_source = inspect.getsource(tasks_mod)
    service_source = inspect.getsource(service_mod)
    combined = task_source + service_source
    assert "kampulynk.export.process" in combined
    assert "kampulynk.email.transactional.tick" in combined
    assert "kampulynk.email.bulk.tick" in combined
    assert "kampulynk.moderation.tick" in combined
    assert "kampulynk.deletion.tick" in combined
    assert "kampulynk.spotlight.tick" in combined
    assert "DataExportBuilder" not in combined
    assert "run_auto_moderation_scan" not in combined
    assert "purge_user" not in combined
    assert "run_learning_spotlight" not in combined
    assert "process_pending_emails" not in combined
    assert "process_pending_bulk_deliveries" not in combined
    assert "send_task" in combined or "apply_async" in combined
