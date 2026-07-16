from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.report.schemas import (
    ReportCreateRequest,
    ReportListResponse,
    ReportResponse,
    ReportReviewRequest,
)
from apps.report.services import (
    create_report_service,
    get_report_details_admin_service,
    list_reports_admin_service,
    review_report_admin_service,
)
from common.enums import ReportEntityType, ReportStatus
from common.pagination import PaginationParams
from common.schemas import ApiResponse
from core.database.session import get_session
from core.security.auth import get_current_app_user, get_current_moderator

router = APIRouter(tags=["8] Reports"])


@router.post(
    "/reports",
    response_model=ApiResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit a report",
    description="Submit a report for a user, post, or comment.",
)
async def create_report_route(
    payload: ReportCreateRequest,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ApiResponse:
    return await create_report_service(db, current_user.id, payload)


@router.get(
    "/admin/reports",
    response_model=ReportListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get reports list",
    description="List and filter reports. Admin/moderator only.",
)
async def list_reports_admin_route(
    current_user=Depends(get_current_moderator),
    db: AsyncSession = Depends(get_session),
    pagination: PaginationParams = Depends(),
    report_status: ReportStatus | None = Query(None, alias="status"),
    entity_type: ReportEntityType | None = Query(None),
    moderator_id: UUID | None = Query(None),
) -> ReportListResponse:
    _ = current_user
    return await list_reports_admin_service(
        db,
        status=report_status,
        entity_type=entity_type,
        moderator_id=moderator_id,
        page=pagination.page,
        page_size=pagination.pageSize,
    )


@router.get(
    "/admin/reports/{report_id}",
    response_model=ReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get report details",
    description="Retrieve details of a report by its ID. Admin/moderator only.",
)
async def get_report_details_admin_route(
    report_id: UUID,
    current_user=Depends(get_current_moderator),
    db: AsyncSession = Depends(get_session),
) -> ReportResponse:
    _ = current_user
    return await get_report_details_admin_service(db, report_id)


@router.patch(
    "/admin/reports",
    response_model=ReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Review report",
    description=(
        "Update a report's status and add moderator comments. "
        "Admin/moderator only. Pass report_id in the JSON body."
    ),
)
async def review_report_admin_route(
    payload: ReportReviewRequest,
    current_user=Depends(get_current_moderator),
    db: AsyncSession = Depends(get_session),
) -> ReportResponse:
    return await review_report_admin_service(db, current_user.id, payload)
