from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.export.builder import DataExportBuilder
from apps.export.claims import claim_export
from apps.export.config import settings as export_settings
from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest
from apps.export.password import generate_export_password
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from core.celery_worker.celery_app import celery_app
from core.celery_worker.config import CeleryTaskQueue
from core.jobs.runtime import AsyncWorkerRuntime, create_worker_runtime

logger = logging.getLogger(__name__)

_PRESIGNED_URL_TTL_SECONDS = 604800  # 7 days


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_export_id(export_id: UUID | str) -> UUID:
    if isinstance(export_id, UUID):
        return export_id
    return UUID(str(export_id))


def _lease_owner_for_task(task: Any, export_id: UUID) -> str:
    task_id = getattr(getattr(task, "request", None), "id", None)
    if task_id:
        return str(task_id)
    return f"export:{export_id}:{uuid4()}"


def _write_temp_zip(data: bytes):
    from apps.export.storage import write_temp_zip

    return write_temp_zip(data)


def _get_export_storage():
    from apps.export.storage import get_export_storage

    return get_export_storage()


async def _queue_export_ready_email(
    *,
    db: AsyncSession,
    export_request: DataExportRequest,
    presigned_url: str,
    zip_password: str,
    storage: Any,
) -> None:
    from apps.export.service import DataExportService

    service = DataExportService(storage=storage)
    await service._queue_ready_email(
        db=db,
        export_request=export_request,
        presigned_url=presigned_url,
        zip_password=zip_password,
    )


async def _complete_export_if_owner(
    db: AsyncSession,
    *,
    export_id: UUID,
    lease_owner: str,
    storage_key: str,
    file_size_bytes: int,
    completed_at: datetime,
    download_expires_at: datetime,
) -> bool:
    """Persist completion only while this worker still owns the lease."""
    result = await db.execute(
        update(DataExportRequest)
        .where(DataExportRequest.id == export_id)
        .where(DataExportRequest.lease_owner == lease_owner)
        .where(DataExportRequest.status == DataExportStatus.processing)
        .values(
            status=DataExportStatus.completed,
            completed_at=completed_at,
            storage_key=storage_key,
            file_size_bytes=file_size_bytes,
            download_expires_at=download_expires_at,
            updated_at=completed_at,
            error_message=None,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def _fail_export_if_owner(
    db: AsyncSession,
    *,
    export_id: UUID,
    lease_owner: str,
    error_message: str,
) -> bool:
    """Record failure only while this worker still owns the lease."""
    failed_at = utc_now()
    result = await db.execute(
        update(DataExportRequest)
        .where(DataExportRequest.id == export_id)
        .where(DataExportRequest.lease_owner == lease_owner)
        .where(DataExportRequest.status == DataExportStatus.processing)
        .values(
            status=DataExportStatus.failed,
            updated_at=failed_at,
            error_message=error_message[:2000],
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def enqueue_export_processing(export_id: UUID | str) -> str | None:
    """Publish export processing after the queued row is committed.

    The Celery payload is the export ID only. A broker failure leaves the
    durable queued row in place for later recovery and must not fail the
    already-accepted API request.
    """
    try:
        parsed_id = _parse_export_id(export_id)
    except (TypeError, ValueError):
        logger.error("Rejected export enqueue with invalid export_id")
        return None

    try:
        async_result = process_export_task.apply_async(
                args=[str(parsed_id)],
                queue=CeleryTaskQueue.EXPORTS_QUEUE.value,
            )
    except Exception:
        logger.exception(
            "Failed to enqueue export processing export_id=%s; record remains queued",
            parsed_id,
        )
        return None

    logger.info(
        "Enqueued export processing export_id=%s task_id=%s",
        parsed_id,
        async_result.id,
    )
    return str(async_result.id)


@celery_app.task(
    name="kampulynk.export.process",
    queue=CeleryTaskQueue.EXPORTS_QUEUE.value,
    bind=True,
    ignore_result=True,
)
def process_export_task(self, export_id: str) -> None:
    """Process a queued data export from a Celery background worker."""
    try:
        parsed_id = _parse_export_id(export_id)
    except (TypeError, ValueError):
        logger.error("Rejected export task with invalid export_id")
        return

    lease_owner = _lease_owner_for_task(self, parsed_id)
    runtime = create_worker_runtime()
    try:
        runtime.runner.run(
            _process_export(
                runtime,
                parsed_id,
                lease_owner=lease_owner,
            )
        )
    finally:
        runtime.close()


async def _process_export(
    runtime: AsyncWorkerRuntime,
    export_id: UUID,
    *,
    lease_owner: str,
    storage: Any | None = None,
) -> None:
    """Claim an export row and run the existing ZIP/upload/email pipeline."""
    export_storage = storage or _get_export_storage()
    temp_path = None
    async with runtime.session_factory() as db:
        claimed = await claim_export(
            db=db,
            export_id=export_id,
            lease_owner=lease_owner,
        )
        if not claimed:
            logger.info("Export %s could not be claimed; skipping", export_id)
            return

        await db.commit()

        export_request = (
            await db.execute(
                select(DataExportRequest).where(DataExportRequest.id == export_id)
            )
        ).scalar_one_or_none()
        if export_request is None:
            logger.error("Export %s not found after claim", export_id)
            return

        try:
            user_id = export_request.user_id
            profile = (
                await db.execute(select(Profile).where(Profile.user_id == user_id))
            ).scalar_one_or_none()

            university_name: str | None = None
            if profile and profile.university_id:
                university = (
                    await db.execute(
                        select(University).where(University.id == profile.university_id)
                    )
                ).scalar_one_or_none()
                if university:
                    university_name = university.name

            zip_password = generate_export_password(
                first_name=profile.first_name if profile else None,
                last_name=profile.last_name if profile else None,
                university=university_name,
                major=profile.major if profile else None,
                minor=profile.minor if profile else None,
            )

            builder = DataExportBuilder(db=db, user_id=user_id, export_id=export_id)
            zip_bytes = await builder.build_encrypted_zip_bytes(zip_password)

            temp_path = _write_temp_zip(zip_bytes)
            storage_key = f"exports/{user_id}/{export_id}.zip"
            export_storage.upload(storage_key, zip_bytes)

            presigned_url = export_storage.generate_download_url(
                storage_key,
                expires_in=_PRESIGNED_URL_TTL_SECONDS,
            )

            completed_at = utc_now()
            download_expires_at = completed_at + timedelta(
                days=export_settings.export_retention_days
            )
            completed = await _complete_export_if_owner(
                db,
                export_id=export_id,
                lease_owner=lease_owner,
                storage_key=storage_key,
                file_size_bytes=len(zip_bytes),
                completed_at=completed_at,
                download_expires_at=download_expires_at,
            )
            if not completed:
                logger.info(
                    "Skipped completing export %s; lease is no longer owned",
                    export_id,
                )
                return

            await db.commit()

            export_request.status = DataExportStatus.completed
            export_request.completed_at = completed_at
            export_request.storage_key = storage_key
            export_request.file_size_bytes = len(zip_bytes)
            export_request.download_expires_at = download_expires_at
            export_request.updated_at = completed_at
            export_request.error_message = None

            await _queue_export_ready_email(
                db=db,
                export_request=export_request,
                presigned_url=presigned_url,
                zip_password=zip_password,
                storage=export_storage,
            )
        except Exception as exc:
            logger.exception("Failed processing export %s", export_id)
            failed = await _fail_export_if_owner(
                db,
                export_id=export_id,
                lease_owner=lease_owner,
                error_message=str(exc)[:2000],
            )
            if failed:
                await db.commit()
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    logger.warning(
                        "Failed deleting temporary export ZIP %s",
                        temp_path,
                        exc_info=True,
                    )
