from __future__ import annotations

import logging

from core.celery_worker.celery_app import celery_app
from core.celery_worker.config import CeleryTaskQueue
from core.jobs.config import settings as reconciliation_settings
from core.jobs.reconciliation_service import run_reconciliation_batch
from core.jobs.runtime import AsyncWorkerRuntime, create_worker_runtime

logger = logging.getLogger(__name__)


async def _run_reconcile_tick(runtime: AsyncWorkerRuntime):
    return await run_reconciliation_batch(
        session_factory=runtime.session_factory,
        engine=runtime.engine,
    )


@celery_app.task(
    name="kampulynk.reconcile.tick",
    queue=CeleryTaskQueue.BACKGROUND_QUEUE.value,
    ignore_result=True,
)
def reconcile_tick() -> None:
    """Synchronous Celery entry point for one durable-work reconciliation tick."""
    if not reconciliation_settings.enabled:
        logger.info(
            "Reconciliation disabled; skipping kampulynk.reconcile.tick without claiming work"
        )
        return

    runtime = create_worker_runtime()
    try:
        outcome = runtime.runner.run(_run_reconcile_tick(runtime))
        logger.info(
            "kampulynk.reconcile.tick complete %s",
            outcome.as_log_dict() if outcome is not None else {},
        )
    finally:
        runtime.close()
