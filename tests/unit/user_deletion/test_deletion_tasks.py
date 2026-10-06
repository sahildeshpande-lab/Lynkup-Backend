from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from core.celery_worker.config import CeleryTaskQueue

import pytest
from celery.schedules import crontab

from apps.moderation.config import settings as auto_moderation_settings
from apps.user_deletion.config import AccountDeletionSettings, settings as deletion_settings
from apps.user_deletion.tasks import deletion_tick
from core.celery_worker.celery_app import celery_app


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


def test_deletion_tick_is_registered_on_background_queue():
    assert deletion_tick.name == "kampulynk.deletion.tick"
    assert "kampulynk.deletion.tick" in celery_app.tasks
    assert _queue_name(deletion_tick) == CeleryTaskQueue.BACKGROUND_QUEUE.value
    assert deletion_tick.ignore_result is True
    assert "apps.user_deletion.tasks" in celery_app.conf.imports


def test_beat_schedule_uses_configured_deletion_interval_not_cron():
    entry = celery_app.conf.beat_schedule["kampulynk.deletion.tick"]
    assert entry["task"] == "kampulynk.deletion.tick"
    expected = float(max(1, deletion_settings.account_deletion_cron_interval_hours) * 3600)
    assert _schedule_seconds(entry) == expected
    assert entry["options"]["queue"] == CeleryTaskQueue.BACKGROUND_QUEUE.value
    assert not isinstance(entry["schedule"], crontab)
    transactional = celery_app.conf.beat_schedule["kampulynk.email.transactional.tick"]
    bulk = celery_app.conf.beat_schedule["kampulynk.email.bulk.tick"]
    moderation = celery_app.conf.beat_schedule["kampulynk.moderation.tick"]
    assert _schedule_seconds(transactional) == 60.0
    assert _schedule_seconds(bulk) == 60.0
    assert _schedule_seconds(moderation) == float(auto_moderation_settings.cron_interval_seconds)


def test_default_deletion_interval_is_24_hours(monkeypatch):
    monkeypatch.delenv("ACCOUNT_DELETION_CRON_INTERVAL_HOURS", raising=False)
    settings = AccountDeletionSettings(_env_file=None)
    assert settings.account_deletion_cron_interval_hours == 24
    assert settings.account_deletion_cron_interval_hours * 3600 == 86400


def test_task_has_no_payload_and_uses_worker_runtime():
    import apps.user_deletion.tasks as tasks_mod

    source = inspect.getsource(tasks_mod)
    assert "create_worker_runtime" in source
    assert "async_session_factory" not in source
    assert "asyncio.run" not in source
    assert "run_purge_batch" in source
    params = list(inspect.signature(deletion_tick.run).parameters)
    forbidden = {"session", "db", "orm", "credentials", "payload", "user_id"}
    assert forbidden.isdisjoint(set(params))
    assert params == []


def test_task_not_validated_only_via_eager_mode():
    assert celery_app.conf.task_always_eager in (False, None)
    assert celery_app.conf.task_ignore_result is True


def test_task_can_publish_to_real_broker():
    from core.celery_worker.config import settings as celery_settings

    try:
        import redis

        client = redis.Redis.from_url(
            celery_settings.celery_broker_url,
            socket_connect_timeout=1,
        )
        client.ping()
    except Exception:
        pytest.skip("Redis broker not available")

    result = deletion_tick.apply_async()
    assert result.id
    assert celery_app.conf.task_always_eager in (False, None)


def test_successful_task_execution_uses_runtime_engine(monkeypatch):
    import apps.user_deletion.tasks as tasks_mod

    seen: dict = {}

    async def fake_run(runtime):
        seen["runtime"] = runtime
        seen["engine"] = runtime.engine
        return SimpleNamespace(eligible=0, purged=0, failed=0, skipped_lock=False)

    class Runtime:
        engine = object()

        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(tasks_mod, "create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr(tasks_mod, "_run_deletion_tick", fake_run)

    deletion_tick.run()

    assert seen["closed"] is True
    assert seen["engine"] is Runtime.engine


def test_task_level_failure_is_visible_to_celery(monkeypatch):
    import apps.user_deletion.tasks as tasks_mod

    async def boom(runtime):
        raise RuntimeError("deletion infrastructure failure")

    class Runtime:
        engine = object()

        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            pass

    monkeypatch.setattr(tasks_mod, "create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr(tasks_mod, "_run_deletion_tick", boom)

    with pytest.raises(RuntimeError, match="infrastructure failure"):
        deletion_tick.run()
