from __future__ import annotations

import logging
from uuid import UUID

from apps.learningspotlight.cron import run_learning_spotlight
from apps.learningspotlight.services.daily_generation_service import LearningSpotlightRunMode
from core.celery_worker.celery_app import celery_app
from core.celery_worker.config import CeleryTaskQueue
from core.jobs.runtime import AsyncWorkerRuntime, create_worker_runtime

logger = logging.getLogger(__name__)


async def _run_spotlight_tick(
    runtime: AsyncWorkerRuntime,
    *,
    run_mode: LearningSpotlightRunMode = "scheduled",
    triggered_by_user_id: UUID | None = None,
    triggered_by_role: str | None = None,
):
    return await run_learning_spotlight(
        engine=runtime.engine,
        session_factory=runtime.session_factory,
        run_mode=run_mode,
        triggered_by_user_id=triggered_by_user_id,
        triggered_by_role=triggered_by_role,
    )


@celery_app.task(
    name="kampulynk.spotlight.tick",
    queue=CeleryTaskQueue.SPOTLIGHTS_QUEUE.value,
)
def spotlight_tick(
    run_mode: str = "scheduled",
    triggered_by_user_id: str | None = None,
    triggered_by_role: str | None = None,
) -> None:
    """Synchronous Celery entry point for Learning Spotlight generation."""
    admin_id: UUID | None = None
    if triggered_by_user_id:
        admin_id = UUID(triggered_by_user_id)
    mode: LearningSpotlightRunMode = (
        "manual" if run_mode == "manual" else "scheduled"
    )
    runtime = create_worker_runtime()
    try:
        outcome = runtime.runner.run(
            _run_spotlight_tick(
                runtime,
                run_mode=mode,
                triggered_by_user_id=admin_id,
                triggered_by_role=triggered_by_role,
            )
        )
        if getattr(outcome, "status", None) == "failed":
            raise RuntimeError("Learning Spotlight daily generation failed")
    finally:
        runtime.close()
