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
    ReportedEntityListResponse,
)
from apps.report.services import (
    create_report_service,
    get_report_details_admin_service,
    get_reported_entities,
    get_reports,
    review_report_admin_service,
)
from common.enums import (
    ReportEntityType,
    ReportStatus,
    ReportedEntityOrder,
    ReportedEntitySort,
)
from common.pagination import PaginationParams
from common.schemas import ApiResponse
from apps.administration.dependencies import require_signed_moderator_or_viewer
from core.database.session import get_session
from core.security.auth import get_current_app_user

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
    summary="List reports for an entity",
    description=(
        "Return individual report records for a specific entity. "
        "Admin/moderator/viewer only."
    ),
)
async def list_reports_admin_route(
    entity_type: ReportEntityType = Query(...),
    entity_id: UUID = Query(...),
    moderator_id: UUID | None = Query(None),
    current_user=Depends(require_signed_moderator_or_viewer),
    db: AsyncSession = Depends(get_session),
    pagination: PaginationParams = Depends(),
) -> ReportListResponse:
    _ = (current_user, moderator_id)
    return await get_reports(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        moderator_id=None,
        page=pagination.page,
        page_size=pagination.pageSize,
    )


@router.get(
    "/admin/reports/details",
    response_model=ReportedEntityListResponse,
    status_code=status.HTTP_200_OK,
    summary="List reported entities",
    description=(
        "Return one row per reported entity for the moderation dashboard, "
        "including previous moderation comments for each entity. "
        "Includes summary counts by status (under_review, actioned, rejected), "
        "total across all statuses, and paginated items. "
        "Optionally filter items by status (under_review, actioned, rejected). "
        "Sort by report_count (default), created_at, updated_at, or "
        "latest_reported_at. order=desc (default) or order=asc. "
        "Admin/moderator/viewer only."
    ),
)
async def get_reported_entities_route(
    entity_type: ReportEntityType = Query(...),
    status_filter: ReportStatus | None = Query(
        None,
        alias="status",
        description="Filter by report status: under_review, actioned, or rejected.",
    ),
    sort: ReportedEntitySort = Query(
        ReportedEntitySort.report_count,
        description=(
            "Sort column. report_count (default, highest first), created_at "
            "(first report time), updated_at (last report update), or "
            "latest_reported_at (newest report)."
        ),
    ),
    order: ReportedEntityOrder = Query(
        ReportedEntityOrder.desc,
        description="Sort direction. desc = highest/newest first (default); asc = lowest/oldest first.",
    ),
    moderator_id: UUID | None = Query(None),
    search: str | None = Query(
        default=None,
        description=(
            "Search reported entities. Post: author first/last name or post content. "
            "User: first/last name or university. Comment: commenter first/last name "
            "or comment text."
        ),
    ),
    current_user=Depends(require_signed_moderator_or_viewer),
    db: AsyncSession = Depends(get_session),
    pagination: PaginationParams = Depends(),
) -> ReportedEntityListResponse:
    return await get_reported_entities(
        db,
        entity_type=entity_type,
        status=status_filter,
        moderator_id=moderator_id,
        page=pagination.page,
        page_size=pagination.pageSize,
        viewer_user_id=current_user.id,
        sort=sort,
        order=order,
        search=search,
    )


@router.get(
    "/admin/reports/{report_id}",
    response_model=ReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get report by ID",
    description="Retrieve details of a report by its ID, including previous moderation comments for the same entity. Admin/moderator/viewer only.",
)
async def get_report_details_admin_route(
    report_id: UUID,
    current_user=Depends(require_signed_moderator_or_viewer),
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
        "Admin/moderator/viewer only. Pass report_id in the JSON body."
    ),
)
async def review_report_admin_route(
    payload: ReportReviewRequest,
    current_user=Depends(require_signed_moderator_or_viewer),
    db: AsyncSession = Depends(get_session),
) -> ReportResponse:
    return await review_report_admin_service(
        db,
        current_user.id,
        payload,
        actor_role=current_user.role,
    )
