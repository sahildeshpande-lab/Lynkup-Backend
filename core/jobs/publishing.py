"""Publish work without blocking the API event loop."""

import asyncio

from core.celery_worker.config import CeleryTaskQueue


async def publish_task(name: str, queue: CeleryTaskQueue, **task_kwargs) -> str:
    from core.celery_worker.celery_app import celery_app

    kwargs = task_kwargs or None
    result = await asyncio.to_thread(
        celery_app.send_task,
        name,
        kwargs=kwargs,
        queue=queue.value,
        retry=False,
    )
    return str(result.id)


async def publish_admin_task(name: str, queue: CeleryTaskQueue, **task_kwargs) -> str:
    from fastapi import HTTPException

    try:
        return await publish_task(name, queue, **task_kwargs)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to queue task. Please retry.") from exc
