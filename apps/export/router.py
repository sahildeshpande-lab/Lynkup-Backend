from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.export.schemas import ExportRequestAcceptedData, ExportStatusData
from apps.export.service import DataExportService, get_data_export_service
from common.schemas import ApiResponse
from core.auth.dependencies import require_recent_auth
from core.database.session import get_session
from core.security.auth import get_current_user

router = APIRouter(prefix="/me/export", tags=["Data Export"])


@router.post("", response_model=ApiResponse)
async def request_data_export(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    _recent_auth: dict = Depends(require_recent_auth),
    db: AsyncSession = Depends(get_session),
    service: DataExportService = Depends(get_data_export_service),
) -> ApiResponse:
    """Request a personal data export.

    Requires recent Firebase authentication (`require_recent_auth`).
    When generation completes, an email with a backend download link is queued
    to ``transactional_email_log`` for cron delivery. The ZIP is stored in
    DigitalOcean Spaces (not emailed as an attachment).
    """
    data: ExportRequestAcceptedData = await service.request_export(
        user=current_user,
        db=db,
    )
    background_tasks.add_task(service.process_export, data.export_id)
    return ApiResponse(
        message="Data export request accepted.",
        data=data.model_dump(mode="json"),
    )


@router.get("/{export_id}", response_model=ApiResponse)
async def get_data_export_status(
    export_id: UUID,
    current_user: User = Depends(get_current_user),
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


@router.get("/{export_id}/download")
async def download_data_export(
    export_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    service: DataExportService = Depends(get_data_export_service),
) -> RedirectResponse:
    """Redirect to a short-lived signed Spaces URL for the export ZIP.

    The Spaces object is retained until retention expiry cleanup; it is not
    deleted merely because a signed URL was generated.
    """
    signed_url = await service.get_download_redirect_url(
        user=current_user,
        export_id=export_id,
        db=db,
    )
    return RedirectResponse(url=signed_url, status_code=302)
