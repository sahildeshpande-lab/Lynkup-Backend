from __future__ import annotations

import logging
from uuid import uuid4

from apps.moderation.config import settings as auto_moderation_settings
from core.celery_worker.celery_app import celery_app
from core.celery_worker.config import CeleryTaskQueue
from core.jobs.runtime import AsyncWorkerRuntime, create_worker_runtime

logger = logging.getLogger(__name__)


def _lease_owner_for_task(task) -> str:
    task_id = getattr(getattr(task, "request", None), "id", None)
    if task_id:
        return str(task_id)
    return f"auto-moderation:{uuid4()}"


async def _run_moderation_tick(
    runtime: AsyncWorkerRuntime,
    *,
    lease_owner: str,
) -> dict[str, int]:
    from apps.moderation.services.auto_moderation_service import run_auto_moderation_scan

    return await run_auto_moderation_scan(
        batch_size=auto_moderation_settings.batch_size,
        session_factory=runtime.session_factory,
        lease_owner=lease_owner,
    )


@celery_app.task(
    name="kampulynk.moderation.tick",
    queue=CeleryTaskQueue.BACKGROUND_QUEUE.value,
    bind=True,
    ignore_result=True,
)
def moderation_tick(self) -> None:
    """Synchronous Celery entry point for one auto-moderation tick."""
    if not auto_moderation_settings.enabled:
        logger.info(
            "Auto-moderation disabled; skipping kampulynk.moderation.tick without claiming work"
        )
        return

    lease_owner = _lease_owner_for_task(self)
    runtime = create_worker_runtime()
    try:
        runtime.runner.run(
            _run_moderation_tick(runtime, lease_owner=lease_owner)
        )
    finally:
        runtime.close()
