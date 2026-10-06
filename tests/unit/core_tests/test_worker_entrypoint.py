from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest
from pydantic import ValidationError

from core.celery_worker.config import CelerySettings, CeleryTaskQueue
from entrypoints import worker


@pytest.fixture
def celery_app(monkeypatch):
    monkeypatch.setattr(worker.celery_settings, "beat_enabled", False)
    app = Mock()
    monkeypatch.setitem(
        sys.modules, "core.celery_worker.celery_app", SimpleNamespace(celery_app=app)
    )
    return app


def test_default_queues(celery_app):
    worker.main([])
    celery_app.worker_main.assert_called_once_with([
        "worker",
        f"--loglevel={worker.WORKER_LOG_LEVEL}",
        f"--pool={worker.celery_settings.worker_pool}",
        f"--concurrency={worker.celery_settings.worker_concurrency}",
        f"--queues={CeleryTaskQueue.queue_names_str()}",
    ])


def test_selected_queues(celery_app):
    first, second = list(CeleryTaskQueue)[:2]
    worker.main([f"{first.value},{second.value}", first.value])
    assert celery_app.worker_main.call_args.args[0][-1] == (
        f"--queues={first.value},{second.value}"
    )


def test_pool_and_concurrency_from_environment(celery_app, monkeypatch):
    monkeypatch.setenv("CELERY_WORKER_POOL", "solo")
    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", "3")
    monkeypatch.setattr(worker, "celery_settings", CelerySettings(_env_file=None))
    worker.main([])
    args = celery_app.worker_main.call_args.args[0]
    assert "--pool=solo" in args
    assert "--concurrency=3" in args


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_invalid_concurrency(value, monkeypatch):
    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", value)
    with pytest.raises(ValidationError, match="worker_concurrency"):
        CelerySettings(_env_file=None)


@pytest.mark.parametrize("enabled", [False, True])
def test_beat_enabled(enabled, celery_app, monkeypatch):
    monkeypatch.setenv("CELERY_BEAT_ENABLED", str(enabled).lower())
    monkeypatch.setattr(worker, "celery_settings", CelerySettings(_env_file=None))
    worker.main([])
    args = celery_app.worker_main.call_args.args[0]
    assert ("--beat" in args) is enabled


def test_beat_enabled_defaults_to_true(monkeypatch):
    monkeypatch.delenv("CELERY_BEAT_ENABLED", raising=False)
    assert CelerySettings(_env_file=None).beat_enabled is True


@pytest.mark.parametrize("flag", ["--queues", "-Q"])
def test_queue_option(flag, celery_app):
    queue = CeleryTaskQueue.BACKGROUND_QUEUE.value
    worker.main([flag, queue])
    assert celery_app.worker_main.call_args.args[0][-1] == f"--queues={queue}"


def test_mixed_queue_arguments_prevent_start(celery_app):
    queue = CeleryTaskQueue.BACKGROUND_QUEUE.value
    with pytest.raises(SystemExit) as exc:
        worker.main([queue, "--queues", queue])
    assert exc.value.code == 2
    celery_app.worker_main.assert_not_called()


@pytest.mark.parametrize("queues", [["unknown"], [""], [CeleryTaskQueue.BACKGROUND_QUEUE.value, "unknown"]])
def test_invalid_queue_prevents_start(queues, celery_app):
    with pytest.raises(RuntimeError, match=r"Unknown queue\(s\)") as exc:
        worker.main(queues)
    assert CeleryTaskQueue.queue_names_str() in str(exc.value)
    celery_app.worker_main.assert_not_called()
