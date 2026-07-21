from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.accounts.db_models import User
from apps.engagement.db_models import Comment
from apps.engagement.repositories.comment_repository import fetch_profiles_by_user_ids
from apps.engagement.services.comment_service import _format_author, _format_comment
from apps.feed.db_models import Post
from apps.feed.services.post_service import format_post_detail
from apps.moderation.services.moderator_assignment_service import (
    _fetch_active_moderator_ids,
    _fetch_superadmin_user_ids,
    assign_next_moderator_round_robin,
)
from apps.profiles.db_models import Profile
from apps.profiles.services import build_user_base_response
from apps.report.db_models import Report
from apps.report.repositories.report_repository import (
    count_reported_entities,
    count_reports as count_report_rows,
    count_reports_by_entity_keys,
    create_report,
    get_duplicate_report,
    get_report_by_id,
    get_reported_entities as fetch_reported_entity_rows,
    get_reports as fetch_report_rows,
    update_report,
)
from apps.report.schemas import (
    EntityReportItem,
    ReportCreateRequest,
    ReportDetailData,
    ReportListData,
    ReportListResponse,
    ReportResponse,
    ReportReviewRequest,
    ReportUserDetail,
    ReportedEntityItem,
    ReportedEntityListData,
    ReportedEntityListResponse,
)
from common.enums import PostState, ReportEntityType, ReportStatus, UserStatus
from common.exceptions import ApiError
from common.pagination import build_paginated_response
from common.responses import error_response, success_response
from common.schemas import ApiResponse
from core.images import generate_profile_image_url

logger = logging.getLogger(__name__)

_VALID_ENTITY_TYPES = (
    ReportEntityType.post,
    ReportEntityType.comment,
    ReportEntityType.user,
)


def _validate_entity_type(entity_type: ReportEntityType) -> None:
    if entity_type not in _VALID_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid entity_type",
        )


def _profile_photo_url(profile: Profile | None) -> str | None:
    if profile is None:
        return None
    photo = getattr(profile, "profile_photo_url", None)
    if not photo:
        return None
    return generate_profile_image_url(photo)


def format_report_detail(
    report: Report,
    reporter_user: User,
    reporter_profile: Profile | None,
    moderator_user: User | None,
    moderator_profile: Profile | None,
    *,
    report_count: int = 0,
) -> ReportDetailData:
    reporter_details = ReportUserDetail(
        id=reporter_user.id,
        first_name=reporter_profile.first_name if reporter_profile else None,
        last_name=reporter_profile.last_name if reporter_profile else None,
        email=reporter_user.email,
        profilePhoto_url=_profile_photo_url(reporter_profile),
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
        report_count=report_count,
    )


def _format_entity_report_item(
    report: Report,
    reporter_user: User,
    reporter_profile: Profile | None,
    moderator_user: User | None,
    moderator_profile: Profile | None,
) -> EntityReportItem:
    reporter_details = ReportUserDetail(
        id=reporter_user.id,
        first_name=reporter_profile.first_name if reporter_profile else None,
        last_name=reporter_profile.last_name if reporter_profile else None,
        email=reporter_user.email,
        profilePhoto_url=_profile_photo_url(reporter_profile),
    )
    moderator_info = None
    if moderator_user is not None:
        moderator_info = ReportUserDetail(
            id=moderator_user.id,
            first_name=moderator_profile.first_name if moderator_profile else None,
            last_name=moderator_profile.last_name if moderator_profile else None,
            email=moderator_user.email,
        )
    return EntityReportItem(
        id=report.id,
        who_reported_id=report.reported_id,
        reason=report.reason,
        status=report.status,
        moderator_id=report.moderator_id,
        admin_comment=report.admin_comment,
        created_at=report.created_at,
        updated_at=report.updated_at,
        reporter_details=reporter_details,
        moderator_info=moderator_info,
    )


async def _resolve_report_moderator_id(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    post: Post | None = None,
) -> UUID | None:
    """
    Assign a moderator for a new report.

    - Post reports reuse the moderator already assigned at publish time.
    - User/comment reports (and posts with no moderator) use the shared
      round-robin cursor; fall back to first active moderator, then superadmin.
    """
    if entity_type == ReportEntityType.post and post is not None and post.moderator_id is not None:
        return post.moderator_id

    try:
        return await assign_next_moderator_round_robin(db)
    except ApiError:
        logger.warning("No active moderators for report assignment; trying fallbacks")
    except Exception:
        logger.exception("Round-robin failed for report assignment; trying fallbacks")

    moderator_ids = await _fetch_active_moderator_ids(db)
    if moderator_ids:
        return moderator_ids[0]

    superadmin_ids = await _fetch_superadmin_user_ids(db)
    if superadmin_ids:
        return superadmin_ids[0]

    return None


async def create_report_service(
    db: AsyncSession,
    user_id: UUID,
    payload: ReportCreateRequest,
) -> ApiResponse:
    if payload.entity_type == ReportEntityType.user and payload.entity_id == user_id:
        return error_response("You cannot report yourself", response_cls=ApiResponse)

    post: Post | None = None

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

    existing = await get_duplicate_report(db, user_id, payload.entity_type, payload.entity_id)
    if existing is not None:
        return error_response("You have already reported this entity", response_cls=ApiResponse)

    moderator_id = await _resolve_report_moderator_id(
        db,
        entity_type=payload.entity_type,
        post=post,
    )

    try:
        await create_report(
            db,
            reported_id=user_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            reason=payload.reason,
            moderator_id=moderator_id,
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


async def get_reports(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    entity_id: UUID,
    moderator_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ReportListResponse:
    """Return individual report records for a single reported entity."""
    _validate_entity_type(entity_type)
    offset = (page - 1) * page_size

    rows = await fetch_report_rows(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        moderator_id=moderator_id,
        offset=offset,
        limit=page_size,
    )
    total_items = await count_report_rows(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        moderator_id=moderator_id,
    )

    items = [
        _format_entity_report_item(row[0], row[1], row[2], row[3], row[4])
        for row in rows
    ]
    paginated = build_paginated_response(items, page, page_size, total_items)

    return success_response(
        message="Reports retrieved successfully.",
        data=ReportListData(
            items=paginated.items,
            page=paginated.page,
            pageSize=paginated.pageSize,
            totalItems=paginated.totalItems,
            totalPages=paginated.totalPages,
        ),
        response_cls=ReportListResponse,
    )


async def _batch_load_posts(
    db: AsyncSession,
    post_ids: list[UUID],
    *,
    viewer_user_id: UUID,
) -> dict[UUID, dict]:
    if not post_ids:
        return {}

    AuthorProfile = aliased(Profile)
    stmt = (
        select(Post, AuthorProfile)
        .outerjoin(AuthorProfile, AuthorProfile.user_id == Post.author_user_id)
        .where(Post.id.in_(post_ids))
    )
    rows = (await db.execute(stmt)).all()
    return {
        post.id: format_post_detail(
            post,
            author_profile=profile,
            viewer_user_id=viewer_user_id,
        )
        for post, profile in rows
    }


async def _batch_load_comments(
    db: AsyncSession,
    comment_ids: list[UUID],
    *,
    viewer_user_id: UUID,
) -> dict[UUID, dict]:
    if not comment_ids:
        return {}

    stmt = select(Comment).where(Comment.id.in_(comment_ids))
    comments = list((await db.execute(stmt)).scalars().all())
    profiles = await fetch_profiles_by_user_ids(
        db,
        [comment.user_id for comment in comments],
    )

    result: dict[UUID, dict] = {}
    for comment in comments:
        profile, university = profiles.get(comment.user_id, (None, None))
        comment_data = _format_comment(
            comment,
            author=_format_author(profile, university),
            user_reaction=None,
            current_user_id=viewer_user_id,
        )
        result[comment.id] = comment_data.model_dump(mode="json")
    return result


async def _batch_load_users(
    db: AsyncSession,
    user_ids: list[UUID],
) -> dict[UUID, dict]:
    if not user_ids:
        return {}

    UserProfile = aliased(Profile)
    stmt = (
        select(User, UserProfile)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(User.id.in_(user_ids))
    )
    rows = (await db.execute(stmt)).all()

    result: dict[UUID, dict] = {}
    for user, profile in rows:
        user_data = await build_user_base_response(user, profile, db)
        if hasattr(user_data, "model_dump"):
            result[user.id] = user_data.model_dump(mode="json")
        else:
            result[user.id] = user_data
    return result


async def _load_entities_for_queue(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    entity_ids: list[UUID],
    viewer_user_id: UUID,
) -> dict[UUID, dict]:
    """Batch-load full entity payloads using existing formatters/services."""
    if entity_type == ReportEntityType.post:
        return await _batch_load_posts(db, entity_ids, viewer_user_id=viewer_user_id)
    if entity_type == ReportEntityType.comment:
        return await _batch_load_comments(db, entity_ids, viewer_user_id=viewer_user_id)
    if entity_type == ReportEntityType.user:
        return await _batch_load_users(db, entity_ids)
    return {}


async def get_reported_entities(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    moderator_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    viewer_user_id: UUID,
) -> ReportedEntityListResponse:
    """
    Return one moderation-dashboard row per reported entity.

    Each item includes the full entity payload plus report metadata.
    """
    _validate_entity_type(entity_type)
    offset = (page - 1) * page_size

    rows = await fetch_reported_entity_rows(
        db,
        entity_type=entity_type,
        moderator_id=moderator_id,
        offset=offset,
        limit=page_size,
    )
    total_items = await count_reported_entities(
        db,
        entity_type=entity_type,
        moderator_id=moderator_id,
    )

    entity_ids = [row["entity_id"] for row in rows]
    entities = await _load_entities_for_queue(
        db,
        entity_type=entity_type,
        entity_ids=entity_ids,
        viewer_user_id=viewer_user_id,
    )

    items = [
        ReportedEntityItem(
            entity=entities.get(row["entity_id"]),
            report_count=row["report_count"],
            latest_reported_at=row["latest_reported_at"],
            moderator_id=row["moderator_id"],
            status=row["status"],
        )
        for row in rows
    ]
    paginated = build_paginated_response(items, page, page_size, total_items)

    return success_response(
        message="Reported entities fetched successfully.",
        data=ReportedEntityListData(
            items=paginated.items,
            page=paginated.page,
            pageSize=paginated.pageSize,
            totalItems=paginated.totalItems,
            totalPages=paginated.totalPages,
        ),
        response_cls=ReportedEntityListResponse,
    )


async def get_report_details_admin_service(
    db: AsyncSession,
    report_id: UUID,
) -> ReportResponse:
    row = await get_report_by_id(db, report_id)
    if row is None:
        return error_response("Report not found", response_cls=ReportResponse)

    report = row[0]
    report_counts = await count_reports_by_entity_keys(
        db,
        [(report.entity_type, report.entity_id)],
    )
    detail = format_report_detail(
        row[0],
        row[1],
        row[2],
        row[3],
        row[4],
        report_count=report_counts.get((report.entity_type, report.entity_id), 0),
    )
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
    report = updated_row[0]
    report_counts = await count_reports_by_entity_keys(
        db,
        [(report.entity_type, report.entity_id)],
    )
    detail = format_report_detail(
        updated_row[0],
        updated_row[1],
        updated_row[2],
        updated_row[3],
        updated_row[4],
        report_count=report_counts.get((report.entity_type, report.entity_id), 0),
    )
    return success_response(
        message="Report reviewed successfully",
        data=detail,
        response_cls=ReportResponse,
    )
