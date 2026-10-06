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
    return f"transactional-email:{uuid4()}"


async def _run_transactional_email_tick(
    runtime: AsyncWorkerRuntime,
    *,
    lease_owner: str,
) -> int:
    from core.email_service import run_transactional_email_tick

    return await run_transactional_email_tick(
        session_factory=runtime.session_factory,
        lease_owner=lease_owner,
    )


@celery_app.task(
    name="kampulynk.email.transactional.tick",
    queue=CeleryTaskQueue.TRANSACTIONAL_QUEUE.value,
    bind=True,
    ignore_result=True,
)
def transactional_email_tick(self) -> None:
    """Synchronous Celery entry point for one transactional-email tick."""
    lease_owner = _lease_owner_for_task(self)
    runtime = create_worker_runtime()
    try:
        runtime.runner.run(
            _run_transactional_email_tick(runtime, lease_owner=lease_owner)
        )
    finally:
        runtime.close()


async def _run_bulk_email_tick(runtime: AsyncWorkerRuntime) -> None:
    from apps.bulk_send.cron import process_bulk_emails

    await process_bulk_emails(session_factory=runtime.session_factory)


@celery_app.task(
    name="kampulynk.email.bulk.tick",
    queue=CeleryTaskQueue.BULK_EMAIL_QUEUE.value,
    ignore_result=True,
)
def bulk_email_tick() -> None:
    """Synchronous Celery entry point for one bulk-email tick."""
    runtime = create_worker_runtime()
    try:
        runtime.runner.run(_run_bulk_email_tick(runtime))
    finally:
        runtime.close()
