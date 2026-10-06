from __future__ import annotations

import asyncio
import inspect
from unittest.mock import patch
from uuid import uuid4

from apps.notifications.tasks import (
    dispatch_campaign_task,
    enqueue_campaign_dispatch,
)
from core.celery_worker.config import CeleryTaskQueue


def test_enqueue_campaign_dispatch_publishes_campaign_id_only():
    campaign_id = uuid4()
    seen: dict = {}

    class FakeResult:
        id = "celery-task-789"

    def fake_apply_async(*, args, kwargs, queue):
        seen["args"] = args
        seen["kwargs"] = kwargs
        seen["queue"] = queue
        return FakeResult()

    with patch.object(dispatch_campaign_task, "apply_async", side_effect=fake_apply_async):
        task_id = enqueue_campaign_dispatch(campaign_id, actor_role="superadmin")

    assert task_id == "celery-task-789"
    assert seen["args"] == [str(campaign_id)]
    assert seen["kwargs"] == {"actor_role": "superadmin"}
    assert seen["queue"] == CeleryTaskQueue.NOTIFICATIONS_QUEUE.value


def test_enqueue_campaign_dispatch_returns_none_on_broker_failure():
    def fake_apply_async(*, args, kwargs, queue):
        raise ConnectionError("redis unavailable")

    with patch.object(dispatch_campaign_task, "apply_async", side_effect=fake_apply_async):
        task_id = enqueue_campaign_dispatch(uuid4())

    assert task_id is None


def test_enqueue_campaign_dispatch_rejects_invalid_campaign_id():
    with patch.object(dispatch_campaign_task, "apply_async") as apply_async:
        task_id = enqueue_campaign_dispatch("not-a-uuid")

    assert task_id is None
    apply_async.assert_not_called()


def test_celery_task_arguments_exclude_message_content():
    args = [str(uuid4())]
    bound = dispatch_campaign_task.s(*args, actor_role="superadmin")
    payload = bound.args
    kwargs = bound.kwargs
    assert payload == tuple(args)
    assert kwargs == {"actor_role": "superadmin"}
    joined = " ".join(str(item) for item in payload)
    assert "ANNOUNCEMENT" not in joined
    assert "TOPIC" not in joined


def test_dispatch_campaign_task_delegates_to_service(monkeypatch):
    seen: dict = {}

    async def fake_dispatch(runtime, campaign_id, *, actor_role):
        seen["campaign_id"] = campaign_id
        seen["actor_role"] = actor_role

    class Runner:
        def run(self, coro):
            return asyncio.run(coro)

    class Runtime:
        runner = Runner()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr("apps.notifications.tasks.create_worker_runtime", lambda: Runtime())
    monkeypatch.setattr("apps.notifications.tasks._dispatch_campaign", fake_dispatch)

    campaign_id = uuid4()
    dispatch_campaign_task.run(str(campaign_id), actor_role="moderator")

    assert seen["campaign_id"] == campaign_id
    assert seen["actor_role"] == "moderator"
    assert seen["closed"] is True


def test_task_registration_and_payload_contains_only_campaign_id():
    assert dispatch_campaign_task.name == "kampulynk.notification.campaign.dispatch"
    queue = getattr(dispatch_campaign_task, "queue", None)
    if queue is None:
        queue = (dispatch_campaign_task._get_exec_options() or {}).get("queue")
    assert queue == CeleryTaskQueue.NOTIFICATIONS_QUEUE.value

    params = list(inspect.signature(dispatch_campaign_task.run).parameters)
    assert "campaign_id" in params
    assert "actor_role" in params


def test_notification_router_publishes_celery_after_commit_not_inline_dispatch():
    from apps.notifications import routes as notification_routes

    source = inspect.getsource(notification_routes)
    assert "enqueue_campaign_dispatch" in source
    assert "asyncio.to_thread" in source
    assert "await dispatch_campaign(" not in source
