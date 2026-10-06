from __future__ import annotations

import logging
from uuid import UUID

from core.celery_worker.celery_app import celery_app
from core.celery_worker.config import CeleryTaskQueue
from core.jobs.runtime import AsyncWorkerRuntime, create_worker_runtime

logger = logging.getLogger(__name__)


def _parse_campaign_id(campaign_id: UUID | str) -> UUID:
    if isinstance(campaign_id, UUID):
        return campaign_id
    return UUID(str(campaign_id))


def enqueue_campaign_dispatch(
    campaign_id: UUID | str,
    *,
    actor_role: str | None = None,
) -> str | None:
    """Publish campaign dispatch after the DRAFT row is committed.

    The Celery payload is the campaign ID only. A broker failure leaves the
    durable DRAFT row in place for later reconciliation and must not fail the
    already-accepted API request.
    """
    try:
        parsed_id = _parse_campaign_id(campaign_id)
    except (TypeError, ValueError):
        logger.error("Rejected campaign dispatch enqueue with invalid campaign_id")
        return None

    try:
        async_result = dispatch_campaign_task.apply_async(
            args=[str(parsed_id)],
            kwargs={"actor_role": actor_role},
            queue=CeleryTaskQueue.NOTIFICATIONS_QUEUE.value,
        )
    except Exception:
        logger.exception(
            "Failed to enqueue campaign dispatch campaign_id=%s; record remains DRAFT",
            parsed_id,
        )
        return None

    logger.info(
        "Enqueued campaign dispatch campaign_id=%s task_id=%s",
        parsed_id,
        async_result.id,
    )
    return str(async_result.id)


@celery_app.task(
    name="kampulynk.notification.campaign.dispatch",
    queue=CeleryTaskQueue.NOTIFICATIONS_QUEUE.value,
    bind=True,
    ignore_result=True,
)
def dispatch_campaign_task(
    self,
    campaign_id: str,
    *,
    actor_role: str | None = None,
) -> None:
    """Process a notification campaign from a Celery worker."""
    try:
        parsed_id = _parse_campaign_id(campaign_id)
    except (TypeError, ValueError):
        logger.error("Rejected campaign dispatch task with invalid campaign_id")
        return

    runtime = create_worker_runtime()
    try:
        runtime.runner.run(
            _dispatch_campaign(runtime, parsed_id, actor_role=actor_role)
        )
    finally:
        runtime.close()


async def _dispatch_campaign(
    runtime: AsyncWorkerRuntime,
    campaign_id: UUID,
    *,
    actor_role: str | None,
) -> None:
    from apps.notifications.services.admin_notification_service import dispatch_campaign

    async with runtime.session_factory() as db:
        await dispatch_campaign(db, campaign_id, actor_role=actor_role)
