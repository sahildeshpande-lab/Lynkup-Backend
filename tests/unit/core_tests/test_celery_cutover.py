"""Cutover tests: Celery Beat is the only scheduled publisher."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from core.celery_worker.config import CeleryTaskQueue

import pytest
from celery.schedules import crontab

from apps.moderation.config import settings as auto_moderation_settings
from apps.user_deletion.config import settings as deletion_settings
from core.celery_worker.celery_app import celery_app
from core.jobs.config import settings as reconciliation_settings
from core.lifespan import lifespan


MIGRATED_TASKS = {
    "kampulynk.recommendations.tick": CeleryTaskQueue.BACKGROUND_QUEUE.value,
    "kampulynk.export.cleanup": CeleryTaskQueue.EXPORTS_QUEUE.value,
    "kampulynk.email.transactional.tick": CeleryTaskQueue.TRANSACTIONAL_QUEUE.value,
    "kampulynk.email.bulk.tick": CeleryTaskQueue.BULK_EMAIL_QUEUE.value,
    "kampulynk.moderation.tick": CeleryTaskQueue.BACKGROUND_QUEUE.value,
    "kampulynk.deletion.tick": CeleryTaskQueue.BACKGROUND_QUEUE.value,
    "kampulynk.spotlight.tick": CeleryTaskQueue.SPOTLIGHTS_QUEUE.value,
    "kampulynk.reconcile.tick": CeleryTaskQueue.BACKGROUND_QUEUE.value,
    "kampulynk.export.process": CeleryTaskQueue.EXPORTS_QUEUE.value,
    "kampulynk.notification.campaign.dispatch": CeleryTaskQueue.NOTIFICATIONS_QUEUE.value,
}

MIGRATED_IMPORTS = {
    "apps.recommendations.tasks",
    "apps.export.cleanup_tasks",
    "core.email_tasks",
    "apps.moderation.tasks",
    "apps.user_deletion.tasks",
    "apps.learningspotlight.tasks",
    "core.jobs.reconcile_tasks",
    "apps.export.tasks",
    "apps.notifications.tasks",
}


def _queue_name(task) -> str:
    queue = getattr(task, "queue", None)
    if queue is None:
        queue = (task._get_exec_options() or {}).get("queue")
    return queue


def test_beat_routes_match_task_queues_consumed_by_workers():
    for module_name in MIGRATED_IMPORTS:
        importlib.import_module(module_name)
    valid_queues = {queue.value for queue in CeleryTaskQueue}
    for entry in celery_app.conf.beat_schedule.values():
        queue = entry["options"]["queue"]
        assert queue in valid_queues
        assert queue == _queue_name(celery_app.tasks[entry["task"]])


def test_tasks_without_explicit_queue_route_to_background():
    route = celery_app.amqp.router.route({}, "kampulynk.test_db")
    assert route["queue"].name == CeleryTaskQueue.BACKGROUND_QUEUE.value


def _schedule_seconds(entry) -> float:
    schedule = entry["schedule"]
    seconds = getattr(schedule, "run_every", schedule)
    if hasattr(seconds, "total_seconds"):
        seconds = seconds.total_seconds()
    return float(seconds)


def test_celery_beat_owns_all_migrated_schedules():
    beat = celery_app.conf.beat_schedule
    transactional = beat["kampulynk.email.transactional.tick"]
    assert transactional["task"] == "kampulynk.email.transactional.tick"
    assert _schedule_seconds(transactional) == 60.0
    assert transactional["options"]["queue"] == CeleryTaskQueue.TRANSACTIONAL_QUEUE.value

    bulk = beat["kampulynk.email.bulk.tick"]
    assert bulk["task"] == "kampulynk.email.bulk.tick"
    assert _schedule_seconds(bulk) == 60.0
    assert bulk["options"]["queue"] == CeleryTaskQueue.BULK_EMAIL_QUEUE.value

    moderation = beat["kampulynk.moderation.tick"]
    assert moderation["task"] == "kampulynk.moderation.tick"
    assert _schedule_seconds(moderation) == float(
        auto_moderation_settings.cron_interval_seconds
    )
    assert moderation["options"]["queue"] == CeleryTaskQueue.BACKGROUND_QUEUE.value

    deletion = beat["kampulynk.deletion.tick"]
    assert deletion["task"] == "kampulynk.deletion.tick"
    assert _schedule_seconds(deletion) == float(
        max(1, deletion_settings.account_deletion_cron_interval_hours) * 3600
    )
    assert deletion["options"]["queue"] == CeleryTaskQueue.BACKGROUND_QUEUE.value

    spotlight = beat["kampulynk.spotlight.tick"]
    assert spotlight["task"] == "kampulynk.spotlight.tick"
    assert isinstance(spotlight["schedule"], crontab)
    assert spotlight["schedule"].minute == {0}
    assert spotlight["schedule"].hour == {0}
    assert spotlight["options"]["queue"] == CeleryTaskQueue.SPOTLIGHTS_QUEUE.value

    reconcile = beat["kampulynk.reconcile.tick"]
    assert reconcile["task"] == "kampulynk.reconcile.tick"
    assert _schedule_seconds(reconcile) == float(reconciliation_settings.interval_seconds)
    assert reconcile["options"]["queue"] == CeleryTaskQueue.BACKGROUND_QUEUE.value

    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True
    assert "kampulynk.export.process" not in beat
    assert transactional["options"]["queue"] != bulk["options"]["queue"]
    assert spotlight["options"]["queue"] not in {
        transactional["options"]["queue"],
        bulk["options"]["queue"],
        moderation["options"]["queue"],
    }


def test_celery_worker_discovers_migrated_tasks_independently_of_fastapi():
    for module_name in MIGRATED_IMPORTS:
        importlib.import_module(module_name)

    assert MIGRATED_IMPORTS.issubset(set(celery_app.conf.imports))
    for name, queue in MIGRATED_TASKS.items():
        assert name in celery_app.tasks
        assert _queue_name(celery_app.tasks[name]) == queue


def test_fastapi_lifespan_does_not_start_apscheduler():
    source = inspect.getsource(lifespan)
    assert "register_jobs" not in source
    assert "scheduler.start()" not in source
    assert "AsyncIOScheduler" not in source
    assert "add_job(" not in source


def test_apscheduler_package_is_removed():
    scheduler_dir = Path(__file__).resolve().parents[3] / "core" / "scheduler"
    assert not (scheduler_dir / "scheduler.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("core.scheduler.scheduler")


def test_no_duplicate_periodic_publisher_in_api_process():
    lifespan_source = inspect.getsource(lifespan)
    assert "process_transactional_emails" not in lifespan_source
    assert "process_bulk_emails" not in lifespan_source
    assert "process_auto_moderation" not in lifespan_source
    assert "process_expired_account_deletions" not in lifespan_source
    assert "run_learning_spotlight" not in lifespan_source
    assert "kampulynk.reconcile" not in lifespan_source


def test_export_router_publishes_celery_after_commit_not_background_tasks():
    from apps.export import router as export_router

    source = inspect.getsource(export_router)
    assert "enqueue_export_processing" in source
    assert "BackgroundTasks" not in source
    assert "add_task" not in source
    assert "kampulynk.export.process" in inspect.getsource(
        importlib.import_module("apps.export.tasks")
    )


def test_requirements_no_longer_depend_on_apscheduler():
    requirements = (
        Path(__file__).resolve().parents[3] / "requirements.txt"
    ).read_text(encoding="utf-8").lower()
    assert "apscheduler" not in requirements
