from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.celery_worker.config import CeleryTaskQueue

import pytest
from celery.schedules import crontab

from apps.learningspotlight.cron import LearningSpotlightRunOutcome
from apps.learningspotlight.tasks import spotlight_tick
from core.celery_worker.celery_app import celery_app
from apps.administration.dependencies import require_signed_admin


def _queue_name(task) -> str:
    queue = getattr(task, "queue", None)
    if queue is None:
        queue = (task._get_exec_options() or {}).get("queue")
    return queue


def test_spotlight_tick_is_registered_on_spotlight_queue():
    assert spotlight_tick.name == "kampulynk.spotlight.tick"
    assert "kampulynk.spotlight.tick" in celery_app.tasks
    assert _queue_name(spotlight_tick) == CeleryTaskQueue.SPOTLIGHTS_QUEUE.value
    assert "apps.learningspotlight.tasks" in celery_app.conf.imports


def test_beat_schedule_is_daily_midnight_utc_crontab():
    entry = celery_app.conf.beat_schedule["kampulynk.spotlight.tick"]
    assert entry["task"] == "kampulynk.spotlight.tick"
    schedule = entry["schedule"]
    assert isinstance(schedule, crontab)
    assert schedule.minute == {0}
    assert schedule.hour == {0}
    assert entry["options"]["queue"] == CeleryTaskQueue.SPOTLIGHTS_QUEUE.value
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True
    assert not hasattr(schedule, "run_every") or getattr(schedule, "run_every", None) is None

    deletion = celery_app.conf.beat_schedule["kampulynk.deletion.tick"]
    moderation = celery_app.conf.beat_schedule["kampulynk.moderation.tick"]
    assert not isinstance(deletion["schedule"], crontab)
    assert not isinstance(moderation["schedule"], crontab)
    assert "kampulynk.export.process" not in {
        item["task"] for item in celery_app.conf.beat_schedule.values()
    }


def test_task_has_no_payload_and_uses_worker_runtime():
    import apps.learningspotlight.tasks as tasks_mod

    source = inspect.getsource(tasks_mod)
    assert "create_worker_runtime" in source
    assert "async_session_factory" not in source
    assert "asyncio.run" not in source
    assert "runtime.engine" in source
    assert "runtime.session_factory" in source
    params = list(inspect.signature(spotlight_tick.run).parameters)
    forbidden = {"session", "db", "orm", "credentials", "payload"}
    assert forbidden.isdisjoint(set(params))
    assert set(params) == {
        "run_mode",
        "triggered_by_user_id",
        "triggered_by_role",
    }


def test_task_not_validated_only_via_eager_mode():
    assert celery_app.conf.task_always_eager in (False, None)


def test_task_propagates_genuine_failure():
    failed = LearningSpotlightRunOutcome(status="failed")

    def _run(coro):
        coro.close()
        return failed

    runtime = SimpleNamespace(
        engine=object(),
        session_factory=object(),
        runner=SimpleNamespace(run=_run),
        close=MagicMock(),
    )
    with patch(
        "apps.learningspotlight.tasks.create_worker_runtime",
        return_value=runtime,
    ):
        with pytest.raises(RuntimeError, match="Learning Spotlight daily generation failed"):
            spotlight_tick()
    runtime.close.assert_called_once()


def test_spotlight_tick_invokes_run_learning_spotlight():
    seen: dict = {}

    async def fake_run(*, engine, session_factory, run_mode="scheduled", **kwargs):
        seen["engine"] = engine
        seen["session_factory"] = session_factory
        seen["run_mode"] = run_mode
        seen["triggered_by_user_id"] = kwargs.get("triggered_by_user_id")
        return LearningSpotlightRunOutcome(status="completed")

    def _run(coro):
        import asyncio

        return asyncio.run(coro)

    runtime = SimpleNamespace(
        engine=object(),
        session_factory=object(),
        runner=SimpleNamespace(run=_run),
        close=MagicMock(),
    )
    with (
        patch(
            "apps.learningspotlight.tasks.create_worker_runtime",
            return_value=runtime,
        ),
        patch(
            "apps.learningspotlight.tasks.run_learning_spotlight",
            new=AsyncMock(side_effect=fake_run),
        ) as run_spotlight,
    ):
        spotlight_tick()

    run_spotlight.assert_awaited_once()
    assert seen["engine"] is runtime.engine
    assert seen["session_factory"] is runtime.session_factory
    assert seen["run_mode"] == "scheduled"
    assert seen["triggered_by_user_id"] is None
    runtime.close.assert_called_once()


def test_task_does_not_raise_on_disabled_or_locked_or_already_completed():
    def _run_with(status):
        def _run(coro):
            coro.close()
            return LearningSpotlightRunOutcome(status=status)

        return _run

    runtime = SimpleNamespace(
        engine=object(),
        session_factory=object(),
        runner=SimpleNamespace(run=MagicMock()),
        close=MagicMock(),
    )
    with patch(
        "apps.learningspotlight.tasks.create_worker_runtime",
        return_value=runtime,
    ):
        for status in ("disabled", "locked", "already_completed", "completed"):
            runtime.runner.run = _run_with(status)
            spotlight_tick()
    assert runtime.close.call_count == 4


@pytest.mark.asyncio
async def test_manual_runcron_defers_generation_to_worker(mock_db) -> None:
    from uuid import uuid4

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from apps.accounts.db_models import User
    from apps.learningspotlight.routes import router as spotlight_router
    from core.database.session import get_session
    from core.security.auth import get_current_admin

    admin_id = uuid4()
    mock_admin = User(id=admin_id, email="admin@example.com", role="superadmin")
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_admin():
        return mock_admin

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin
    app.dependency_overrides[get_session] = _override_db

    with (
        patch(
            "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService.try_claim_manual_spotlight_run",
            new=AsyncMock(return_value=True),
        ),
        patch("core.jobs.publishing.publish_admin_task", new=AsyncMock(return_value="spotlight-task")) as publish,
        patch("apps.learningspotlight.cron.run_learning_spotlight", new=AsyncMock()) as generate,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/admin/spotlight/runcron")
        assert resp.status_code == 202
        assert resp.json()["data"] == {"task_id": "spotlight-task", "status": "queued"}
        publish.assert_awaited_once()
        assert publish.await_args.args[0] == "kampulynk.spotlight.tick"
        assert publish.await_args.kwargs["run_mode"] == "manual"
        generate.assert_not_awaited()
