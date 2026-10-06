"""Celery owns scheduled and manually requested export retention cleanup."""

from sqlalchemy import text

from core.celery_worker.celery_app import celery_app
from core.celery_worker.config import CeleryTaskQueue
from core.jobs.runtime import create_worker_runtime


async def _run_cleanup(runtime):
    from apps.export.cleanup import cleanup_expired_exports

    async with runtime.engine.begin() as connection:
        locked = (await connection.execute(
            text("SELECT pg_try_advisory_xact_lock(742893481)")
        )).scalar_one()
        if not locked:
            return 0
        return await cleanup_expired_exports(session_factory=runtime.session_factory)


@celery_app.task(
    name="kampulynk.export.cleanup",
    queue=CeleryTaskQueue.EXPORTS_QUEUE.value,
    ignore_result=True,
)
def cleanup_exports():
    runtime = create_worker_runtime()
    try:
        runtime.runner.run(_run_cleanup(runtime))
    finally:
        runtime.close()
