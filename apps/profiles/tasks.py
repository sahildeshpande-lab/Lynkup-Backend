"""Celery tasks for profile-related background work."""

from __future__ import annotations

import logging
from uuid import uuid4

from core.celery_worker.celery_app import celery_app
from core.celery_worker.config import CeleryTaskQueue
from core.jobs.runtime import AsyncWorkerRuntime, create_worker_runtime

logger = logging.getLogger(__name__)


def _lease_owner_for_task(task) -> str:
    task_id = getattr(getattr(task, "request", None), "id", None)
    if task_id:
        return str(task_id)
    return f"graduation-email:{uuid4()}"


async def _run_graduation_email_tick(
    runtime: AsyncWorkerRuntime,
    *,
    lease_owner: str,
) -> dict[str, int]:
    from apps.profiles.services.graduation_email_service import run_graduation_email_tick

    return await run_graduation_email_tick(
        session_factory=runtime.session_factory,
        lease_owner=lease_owner,
    )


@celery_app.task(
    name="kampulynk.graduation.tick",
    queue=CeleryTaskQueue.TRANSACTIONAL_QUEUE.value,
    bind=True,
    ignore_result=True,
)
def graduation_email_tick(self) -> None:
    """Queue and deliver graduation completion emails only."""
    lease_owner = _lease_owner_for_task(self)
    runtime = create_worker_runtime()
    try:
        runtime.runner.run(
            _run_graduation_email_tick(runtime, lease_owner=lease_owner)
        )
    finally:
        runtime.close()
