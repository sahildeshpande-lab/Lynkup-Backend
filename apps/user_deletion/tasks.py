from __future__ import annotations

import logging

from apps.user_deletion.services.account_deletion_service import AccountDeletionService
from core.celery_worker.celery_app import celery_app
from core.celery_worker.config import CeleryTaskQueue
from core.jobs.runtime import AsyncWorkerRuntime, create_worker_runtime

logger = logging.getLogger(__name__)


async def _run_deletion_tick(runtime: AsyncWorkerRuntime):
    return await AccountDeletionService.run_purge_batch(engine=runtime.engine)


@celery_app.task(
    name="kampulynk.deletion.tick",
    queue=CeleryTaskQueue.BACKGROUND_QUEUE.value,
    ignore_result=True,
)
def deletion_tick() -> None:
    """Synchronous Celery entry point for one account-deletion purge tick."""
    runtime = create_worker_runtime()
    try:
        runtime.runner.run(_run_deletion_tick(runtime))
    finally:
        runtime.close()
