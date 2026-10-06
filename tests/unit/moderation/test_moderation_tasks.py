from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from core.celery_worker.config import CeleryTaskQueue

import pytest
from celery.schedules import crontab

from apps.moderation.config import AutoModerationSettings, settings as auto_moderation_settings
from apps.moderation.tasks import moderation_tick
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


def test_moderation_tick_is_registered_on_background_queue():
    assert moderation_tick.name == "kampulynk.moderation.tick"
    assert "kampulynk.moderation.tick" in celery_app.tasks
    assert _queue_name(moderation_tick) == CeleryTaskQueue.BACKGROUND_QUEUE.value
    assert moderation_tick.ignore_result is True
    assert "apps.moderation.tasks" in celery_app.conf.imports


def test_beat_schedule_uses_configured_interval_not_cron():
    entry = celery_app.conf.beat_schedule["kampulynk.moderation.tick"]
    assert entry["task"] == "kampulynk.moderation.tick"
    assert _schedule_seconds(entry) == float(auto_moderation_settings.cron_interval_seconds)
    assert entry["options"]["queue"] == CeleryTaskQueue.BACKGROUND_QUEUE.value
    assert not isinstance(entry["schedule"], crontab)
    transactional = celery_app.conf.beat_schedule["kampulynk.email.transactional.tick"]
    bulk = celery_app.conf.beat_schedule["kampulynk.email.bulk.tick"]
    assert _schedule_seconds(transactional) == 60.0
    assert _schedule_seconds(bulk) == 60.0


def test_default_moderation_interval_is_120_without_override(monkeypatch):
    monkeypatch.delenv("AUTO_MODERATION_CRON_INTERVAL_SECONDS", raising=False)
    settings = AutoModerationSettings(_env_file=None)
    assert settings.cron_interval_seconds == 120


def test_task_module_uses_worker_owned_runtime_not_api_session():
    import apps.moderation.tasks as tasks_mod

    source = inspect.getsource(tasks_mod)
    assert "create_worker_runtime" in source
    assert "async_session_factory" not in source
    assert "asyncio.run" not in source
    params = list(inspect.signature(moderation_tick.run).parameters)
    forbidden = {"session", "db", "orm", "blacklist", "credentials", "payload"}
    assert forbidden.isdisjoint(set(params))


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

    result = moderation_tick.apply_async()
    assert result.id
    assert celery_app.conf.task_always_eager in (False, None)


def test_disabled_moderation_skips_without_claiming(monkeypatch):
    import apps.moderation.tasks as tasks_mod

    seen = {"runtime": False, "run": False}

    monkeypatch.setattr(
        tasks_mod,
        "auto_moderation_settings",
        SimpleNamespace(enabled=False, batch_size=50, cron_interval_seconds=120),
    )
    monkeypatch.setattr(
        tasks_mod,
        "create_worker_runtime",
        lambda: seen.__setitem__("runtime", True) or (_ for _ in ()).throw(AssertionError("runtime")),
    )

    async def fake_run(*args, **kwargs):
        seen["run"] = True
        return {"posts": 0, "comments": 0}

    monkeypatch.setattr(tasks_mod, "_run_moderation_tick", fake_run)

    moderation_tick.push_request(id="celery-task-moderation-disabled")
    try:
        moderation_tick.run()
    finally:
        moderation_tick.pop_request()

    assert seen["runtime"] is False
    assert seen["run"] is False


def test_successful_task_execution_uses_runtime(monkeypatch):
    import apps.moderation.tasks as tasks_mod

    seen: dict = {}

    async def fake_run(runtime, *, lease_owner):
        seen["lease_owner"] = lease_owner
        seen["runtime"] = runtime
        return {"posts": 0, "comments": 0}

    class Runtime:
        session_factory = object()

        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(
        tasks_mod,
        "auto_moderation_settings",
        SimpleNamespace(enabled=True, batch_size=50, cron_interval_seconds=120),
    )
    monkeypatch.setattr(tasks_mod, "create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr(tasks_mod, "_run_moderation_tick", fake_run)

    moderation_tick.push_request(id="celery-task-moderation-1")
    try:
        moderation_tick.run()
    finally:
        moderation_tick.pop_request()

    assert seen["lease_owner"] == "celery-task-moderation-1"
    assert seen["closed"] is True
    assert seen["runtime"] is not None


def test_task_level_failure_is_visible_to_celery(monkeypatch):
    import apps.moderation.tasks as tasks_mod

    async def boom(runtime, *, lease_owner):
        raise RuntimeError("moderation infrastructure failure")

    class Runtime:
        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            pass

    monkeypatch.setattr(
        tasks_mod,
        "auto_moderation_settings",
        SimpleNamespace(enabled=True, batch_size=50, cron_interval_seconds=120),
    )
    monkeypatch.setattr(tasks_mod, "create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr(tasks_mod, "_run_moderation_tick", boom)

    moderation_tick.push_request(id="celery-task-moderation-fail")
    try:
        with pytest.raises(RuntimeError, match="infrastructure failure"):
            moderation_tick.run()
    finally:
        moderation_tick.pop_request()


def test_empty_work_is_a_successful_outcome(monkeypatch):
    import apps.moderation.tasks as tasks_mod

    seen = {"closed": False}

    async def fake_run(runtime, *, lease_owner):
        return {"posts": 0, "comments": 0}

    class Runtime:
        class Runner:
            def run(self, coro):
                return asyncio.run(coro)

        runner = Runner()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(
        tasks_mod,
        "auto_moderation_settings",
        SimpleNamespace(enabled=True, batch_size=50, cron_interval_seconds=120),
    )
    monkeypatch.setattr(tasks_mod, "create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr(tasks_mod, "_run_moderation_tick", fake_run)

    moderation_tick.run()
    assert seen["closed"] is True
