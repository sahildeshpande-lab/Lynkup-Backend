from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends
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

    Requires recent Firebase authentication (``require_recent_auth``).

    When generation completes the background task:
    1. Builds an AES-256 password-protected ZIP.
    2. Uploads it to DigitalOcean Spaces at ``exports/<user_id>/<export_id>.zip``.
    3. Generates a 7-day presigned Spaces URL.
    4. Queues an email containing the presigned URL and 6-character ZIP password
       directly to the user.

    There is no backend download endpoint.  The email link goes directly to the
    DigitalOcean Spaces presigned URL.
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

