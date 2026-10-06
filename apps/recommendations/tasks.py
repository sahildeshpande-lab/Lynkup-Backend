"""Scheduled and manually requested recommendation generation."""

from sqlalchemy import text

from core.celery_worker.celery_app import celery_app
from core.celery_worker.config import CeleryTaskQueue
from core.jobs.runtime import create_worker_runtime


async def _run_recommendations(runtime):
    from apps.recommendations.services.recommendation_cron_service import RecommendationCronService

    # Dedicated transaction holds the lock across all per-user commits.
    async with runtime.engine.begin() as connection:
        locked = (await connection.execute(
            text("SELECT pg_try_advisory_xact_lock(742893480)")
        )).scalar_one()
        if not locked:
            return False
        return await RecommendationCronService.run_recommendation_generation(
            session_factory=runtime.session_factory
        )


@celery_app.task(
    name="kampulynk.recommendations.tick",
    queue=CeleryTaskQueue.BACKGROUND_QUEUE.value,
    ignore_result=True,
)
def recommendations_tick():
    runtime = create_worker_runtime()
    try:
        runtime.runner.run(_run_recommendations(runtime))
    finally:
        runtime.close()
