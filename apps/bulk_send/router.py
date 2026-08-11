from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.bulk_send.schemas import (
    AttachmentUploadResponse,
    CampaignDetailResponse,
    CampaignListResponse,
    CreateBulkCampaignRequest,
    CreateCampaignResponse,
)
from apps.bulk_send.service import BulkSendService, get_bulk_send_service
from common.exceptions import ApiError
from common.responses import error_response, success_response
from core.database.session import get_session
from core.security.auth import get_current_admin

router = APIRouter(prefix="/admin/bulk-send", tags=["Bulk Send Email"])


@router.post(
    "/attachments",
    response_model=AttachmentUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload a bulk email attachment",
)
async def upload_bulk_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_admin),
    service: BulkSendService = Depends(get_bulk_send_service),
) -> AttachmentUploadResponse:
    try:
        data = await service.upload_attachment(admin=current_user, file=file)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Attachment upload failed"
        return error_response(detail, response_cls=AttachmentUploadResponse)
    except RuntimeError as exc:
        return error_response(str(exc), response_cls=AttachmentUploadResponse)

    return success_response(
        "Attachment uploaded.",
        data=data,
        response_cls=AttachmentUploadResponse,
    )


@router.post(
    "/campaigns",
    response_model=CreateCampaignResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a bulk email campaign",
)
async def create_bulk_campaign(
    payload: CreateBulkCampaignRequest,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
    service: BulkSendService = Depends(get_bulk_send_service),
) -> CreateCampaignResponse:
    try:
        data = await service.create_campaign(admin=current_user, payload=payload, db=db)
    except ApiError as exc:
        return error_response(exc.message, response_cls=CreateCampaignResponse)

    return success_response(
        "Bulk email campaign queued.",
        data=data,
        response_cls=CreateCampaignResponse,
    )


@router.get(
    "/campaigns",
    response_model=CampaignListResponse,
    status_code=status.HTTP_200_OK,
    summary="List bulk email campaigns",
)
async def list_bulk_campaigns(
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
    service: BulkSendService = Depends(get_bulk_send_service),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=200),
) -> CampaignListResponse:
    _ = current_user
    data = await service.list_campaigns_page(db=db, page=page, page_size=pageSize)
    return success_response(
        "Bulk email campaigns fetched successfully.",
        data=data,
        response_cls=CampaignListResponse,
    )


@router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get bulk email campaign detail",
)
async def get_bulk_campaign(
    campaign_id: UUID,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
    service: BulkSendService = Depends(get_bulk_send_service),
) -> CampaignDetailResponse:
    _ = current_user
    try:
        data = await service.get_campaign_detail(db=db, campaign_id=campaign_id)
    except ApiError as exc:
        return error_response(exc.message, response_cls=CampaignDetailResponse)

    return success_response(
        "Bulk email campaign fetched successfully.",
        data=data,
        response_cls=CampaignDetailResponse,
    )
