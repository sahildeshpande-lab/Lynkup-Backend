from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.export.schemas import ExportRequestAcceptedData, ExportStatusData
from apps.export.service import DataExportService, get_data_export_service
from apps.export.tasks import enqueue_export_processing
from common.schemas import ApiResponse
from core.auth.dependencies import require_recent_auth
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_user
from core.security.mobile.dependencies import require_mobile_request_security

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me/export", tags=["Data Export"])
admin_router = APIRouter(prefix="/admin/exports", tags=["Data Export"])


@router.post("", response_model=ApiResponse)
async def request_data_export(
    current_user: User = Depends(require_mobile_request_security),
    _recent_auth: dict = Depends(require_recent_auth),
    db: AsyncSession = Depends(get_session),
    service: DataExportService = Depends(get_data_export_service),
) -> ApiResponse:
    """Request a personal data export.

    Requires a revoked-checked Firebase ID token (``require_recent_auth``).

    After the queued export row is committed, processing is published to the
    Celery ``background`` queue. When generation completes the worker:

    1. Builds an AES-256 password-protected ZIP.
    2. Uploads it to DigitalOcean Spaces at ``exports/<user_id>/<export_id>.zip``.
    3. Generates a 7-day presigned Spaces origin URL (HTTPS, virtual-hosted).
    4. Queues an email containing the presigned URL and 6-character ZIP password
       directly to the user.

    There is no backend download endpoint.  The email link goes directly to the
    DigitalOcean Spaces origin presigned URL.
    """
    data: ExportRequestAcceptedData = await service.request_export(
        user=current_user,
        db=db,
    )
    try:
        await asyncio.to_thread(enqueue_export_processing, data.export_id)
    except Exception:
        logger.exception(
            "Failed to publish export %s to Celery; record remains queued",
            data.export_id,
        )
    return ApiResponse(
        message="Data export request accepted.",
        data=data.model_dump(mode="json"),
    )


@router.get("/{export_id}", response_model=ApiResponse)
async def get_data_export_status(
    export_id: UUID,
    current_user: User = Depends(require_mobile_request_security),
    db: AsyncSession = Depends(get_session),
    service: DataExportService = Depends(get_data_export_service),
) -> ApiResponse:
    data: ExportStatusData = await service.get_export_status(
        user=current_user,
        export_id=export_id,
        db=db,
    )
    return ApiResponse(
        message="Export status fetched successfully.",
        data=data.model_dump(mode="json"),
    )


@admin_router.post("/cleanup", response_model=ApiResponse, status_code=202)
async def cleanup_expired_data_exports(
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Queue expired export cleanup for a Celery worker."""
    from core.celery_worker.config import CeleryTaskQueue
    from core.jobs.publishing import publish_admin_task
    from apps.administration.services.admin_activity_log_service import create_admin_activity_log

    task_id = await publish_admin_task("kampulynk.export.cleanup", CeleryTaskQueue.EXPORTS_QUEUE)
    await create_admin_activity_log(
        db, user_id=current_user.id, role=current_user.role, action="queue",
        module="export", record_id=None,
        description="queued expired export cleanup",
        metadata={"task_id": task_id}, commit=True,
    )
    return ApiResponse(
        message="Expired export cleanup queued.",
        data={"task_id": task_id, "status": "queued"},
    )
