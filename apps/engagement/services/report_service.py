from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.engagement.db_models import Comment, Report
from apps.engagement.repositories.report_repository import (
    count_reports,
    create_report,
    get_duplicate_report,
    get_report_by_id,
    get_reports,
    update_report,
)
from apps.engagement.schemas import (
    ReportCreateRequest,
    ReportDetailData,
    ReportListData,
    ReportListResponse,
    ReportResponse,
    ReportReviewRequest,
    ReportUserDetail,
)
from apps.feed.db_models import Post
from apps.profiles.db_models import Profile
from common.enums import PostState, ReportEntityType, ReportStatus, UserStatus
from common.pagination import build_paginated_response
from common.responses import error_response, success_response
from common.schemas import ApiResponse

logger = logging.getLogger(__name__)


def format_report_detail(
    report: Report,
    reporter_user: User,
    reporter_profile: Profile | None,
    moderator_user: User | None,
    moderator_profile: Profile | None,
) -> ReportDetailData:
    reporter_details = ReportUserDetail(
        id=reporter_user.id,
        first_name=reporter_profile.first_name if reporter_profile else None,
        last_name=reporter_profile.last_name if reporter_profile else None,
        email=reporter_user.email,
    )

    moderator_info = None
    if moderator_user is not None:
        moderator_info = ReportUserDetail(
            id=moderator_user.id,
            first_name=moderator_profile.first_name if moderator_profile else None,
            last_name=moderator_profile.last_name if moderator_profile else None,
            email=moderator_user.email,
        )

    return ReportDetailData(
        id=report.id,
        reported_id=report.reported_id,
        entity_type=report.entity_type,
        entity_id=report.entity_id,
        reason=report.reason,
        status=report.status,
        moderator_id=report.moderator_id,
        admin_comment=report.admin_comment,
        created_at=report.created_at,
        updated_at=report.updated_at,
        reporter_details=reporter_details,
        moderator_info=moderator_info,
    )


async def create_report_service(
    db: AsyncSession,
    user_id: UUID,
    payload: ReportCreateRequest,
) -> ApiResponse:
    # 1. Prevent self-reporting
    if payload.entity_type == ReportEntityType.user and payload.entity_id == user_id:
        return error_response("You cannot report yourself", response_cls=ApiResponse)

    # 2. Validate entity existence and soft-deletion status
    if payload.entity_type == ReportEntityType.user:
        user = (
            await db.execute(select(User).where(User.id == payload.entity_id))
        ).scalar_one_or_none()
        if user is None:
            return error_response("User does not exist", response_cls=ApiResponse)
        if user.is_deleted or user.deleted_at is not None or user.status == UserStatus.deleting:
            return error_response("Cannot report a soft-deleted user", response_cls=ApiResponse)

    elif payload.entity_type == ReportEntityType.post:
        post = (
            await db.execute(select(Post).where(Post.id == payload.entity_id))
        ).scalar_one_or_none()
        if post is None:
            return error_response("Post does not exist", response_cls=ApiResponse)
        if post.state == PostState.deleted:
            return error_response("Cannot report a soft-deleted post", response_cls=ApiResponse)

    elif payload.entity_type == ReportEntityType.comment:
        comment = (
            await db.execute(select(Comment).where(Comment.id == payload.entity_id))
        ).scalar_one_or_none()
        if comment is None:
            return error_response("Comment does not exist", response_cls=ApiResponse)
        if comment.is_deleted:
            return error_response("Cannot report a soft-deleted comment", response_cls=ApiResponse)

    # 3. Prevent duplicate reports
    existing = await get_duplicate_report(db, user_id, payload.entity_type, payload.entity_id)
    if existing is not None:
        return error_response("You have already reported this entity", response_cls=ApiResponse)

    # 4. Create and save report
    try:
        await create_report(
            db,
            reported_id=user_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            reason=payload.reason,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create report for user_id=%s entity_type=%s entity_id=%s",
            user_id,
            payload.entity_type,
            payload.entity_id,
        )
        return error_response("Failed to submit report", response_cls=ApiResponse)

    return success_response(
        message="Report submitted successfully.",
        data={},
        response_cls=ApiResponse,
    )


async def list_reports_admin_service(
    db: AsyncSession,
    *,
    status: ReportStatus | None = None,
    entity_type: ReportEntityType | None = None,
    moderator_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ReportListResponse:
    offset = (page - 1) * page_size

    rows = await get_reports(
        db,
        status=status,
        entity_type=entity_type,
        moderator_id=moderator_id,
        offset=offset,
        limit=page_size,
    )
    total_items = await count_reports(
        db,
        status=status,
        entity_type=entity_type,
        moderator_id=moderator_id,
    )

    items = [
        format_report_detail(row[0], row[1], row[2], row[3], row[4])
        for row in rows
    ]

    paginated = build_paginated_response(items, page, page_size, total_items)

    return success_response(
        message="Reports retrieved successfully",
        data=ReportListData(
            items=paginated.items,
            page=paginated.page,
            pageSize=paginated.pageSize,
            totalItems=paginated.totalItems,
            totalPages=paginated.totalPages,
        ),
        response_cls=ReportListResponse,
    )


async def get_report_details_admin_service(
    db: AsyncSession,
    report_id: UUID,
) -> ReportResponse:
    row = await get_report_by_id(db, report_id)
    if row is None:
        return error_response("Report not found", response_cls=ReportResponse)

    detail = format_report_detail(row[0], row[1], row[2], row[3], row[4])
    return success_response(
        message="Report details retrieved successfully",
        data=detail,
        response_cls=ReportResponse,
    )


async def review_report_admin_service(
    db: AsyncSession,
    current_admin_id: UUID,
    payload: ReportReviewRequest,
) -> ReportResponse:
    report_id = payload.report_id
    row = await get_report_by_id(db, report_id)
    if row is None:
        return error_response("Report not found", response_cls=ReportResponse)

    try:
        await update_report(
            db,
            report_id=report_id,
            status=payload.status,
            admin_comment=payload.admin_comment,
            moderator_id=current_admin_id,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to review report report_id=%s", report_id)
        return error_response("Failed to update report", response_cls=ReportResponse)

    updated_row = await get_report_by_id(db, report_id)
    detail = format_report_detail(
        updated_row[0],
        updated_row[1],
        updated_row[2],
        updated_row[3],
        updated_row[4],
    )
    return success_response(
        message="Report reviewed successfully",
        data=detail,
        response_cls=ReportResponse,
    )
