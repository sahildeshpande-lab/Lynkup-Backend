from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.accounts.db_models import User
from apps.engagement.db_models import Comment
from apps.engagement.repositories.comment_repository import (
    fetch_profiles_by_user_ids,
    get_comment_by_id,
    mark_comment_deleted,
)
from apps.engagement.services.comment_service import _format_author, _format_comment
from apps.feed.db_models import Post
from apps.feed.services.post_service import _resolve_moderator_name, format_post_detail
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
    get_previous_report_comments,
    get_previous_report_comments_for_entities,
    get_report_by_id,
    get_reported_entities as fetch_reported_entity_rows,
    get_reports as fetch_report_rows,
    update_report,
)
from apps.report.schemas import (
    EntityReportItem,
    PreviousCommentItem,
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
    is_report_reviewed,
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
    previous_comments: list[PreviousCommentItem] | None = None,
) -> ReportDetailData:
    reporter_details = ReportUserDetail(
        id=reporter_user.id,
        first_name=reporter_profile.first_name if reporter_profile else None,
        last_name=reporter_profile.last_name if reporter_profile else None,
        email=reporter_user.email,
        profilePhoto_url=_profile_photo_url(reporter_profile),
    )

    moderator_info = None
    moderator_name = None
    if moderator_user is not None:
        moderator_info = ReportUserDetail(
            id=moderator_user.id,
            first_name=moderator_profile.first_name if moderator_profile else None,
            last_name=moderator_profile.last_name if moderator_profile else None,
            email=moderator_user.email,
        )
        moderator_name = _resolve_moderator_name(moderator_user, moderator_profile)

    reviewed = is_report_reviewed(report.status)
    return ReportDetailData(
        id=report.id,
        reported_id=report.reported_id,
        entity_type=report.entity_type,
        entity_id=report.entity_id,
        reason=report.reason,
        status=report.status,
        moderator_id=report.moderator_id,
        moderator_name=moderator_name,
        admin_comment=report.admin_comment,
        created_at=report.created_at,
        updated_at=report.updated_at,
        reporter_details=reporter_details,
        moderator_info=moderator_info,
        report_count=report_count,
        is_reviewed=reviewed,
        previous_comments=previous_comments,
    )


def _format_previous_comments(
    rows: list[tuple[Report, User | None, Profile | None]],
) -> list[PreviousCommentItem] | None:
    if not rows:
        return None
    return [
        PreviousCommentItem(
            moderator_id=report.moderator_id,
            moderator_name=_resolve_moderator_name(moderator_user, moderator_profile)
            if moderator_user is not None
            else None,
            updated_at=report.updated_at,
            admin_comment=report.admin_comment,
        )
        for report, moderator_user, moderator_profile in rows
    ]


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
    moderator_name = None
    if moderator_user is not None:
        moderator_info = ReportUserDetail(
            id=moderator_user.id,
            first_name=moderator_profile.first_name if moderator_profile else None,
            last_name=moderator_profile.last_name if moderator_profile else None,
            email=moderator_user.email,
        )
        moderator_name = _resolve_moderator_name(moderator_user, moderator_profile)
    reviewed = is_report_reviewed(report.status)
    return EntityReportItem(
        id=report.id,
        who_reported_id=report.reported_id,
        reason=report.reason,
        status=report.status,
        moderator_id=report.moderator_id,
        moderator_name=moderator_name,
        admin_comment=report.admin_comment,
        created_at=report.created_at,
        updated_at=report.updated_at,
        reporter_details=reporter_details,
        moderator_info=moderator_info,
        is_reviewed=reviewed,
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

    moderator_id = await _resolve_report_moderator_id(
        db,
        entity_type=payload.entity_type,
        post=post,
    )

    try:
        report = await create_report(
            db,
            reported_id=user_id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            reason=payload.reason,
            moderator_id=moderator_id,
        )
        await db.commit()
        await db.refresh(report)
    except Exception:
        await db.rollback()
        logger.exception(
            "Failed to create report for user_id=%s entity_type=%s entity_id=%s",
            user_id,
            payload.entity_type,
            payload.entity_id,
        )
        return error_response("Failed to submit report", response_cls=ApiResponse)

    reviewed = is_report_reviewed(report.status)
    moderator_name = None
    if report.moderator_id is not None:
        names = await _batch_moderator_names(db, [report.moderator_id])
        moderator_name = names.get(report.moderator_id)

    return success_response(
        message="Report submitted successfully.",
        data={
            "id": report.id,
            "who_reported_id": report.reported_id,
            "entity_type": report.entity_type,
            "entity_id": report.entity_id,
            "reason": report.reason,
            "status": report.status,
            "moderator_id": report.moderator_id,
            "moderator_name": moderator_name,
            "admin_comment": report.admin_comment,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
            "is_reviewed": reviewed,
        },
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
    ModeratorUser = aliased(User)
    ModeratorProfile = aliased(Profile)
    stmt = (
        select(Post, AuthorProfile, ModeratorUser, ModeratorProfile)
        .outerjoin(AuthorProfile, AuthorProfile.user_id == Post.author_user_id)
        .outerjoin(ModeratorUser, ModeratorUser.id == Post.moderator_id)
        .outerjoin(ModeratorProfile, ModeratorProfile.user_id == Post.moderator_id)
        .where(Post.id.in_(post_ids))
    )
    rows = (await db.execute(stmt)).all()
    return {
        post.id: format_post_detail(
            post,
            author_profile=author_profile,
            moderator_user=moderator_user,
            moderator_profile=moderator_profile,
            viewer_user_id=viewer_user_id,
        )
        for post, author_profile, moderator_user, moderator_profile in rows
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


async def _batch_moderator_names(
    db: AsyncSession,
    moderator_ids: list[UUID],
) -> dict[UUID, str | None]:
    """Resolve display names for report-assigned moderators."""
    unique_ids = list({mid for mid in moderator_ids if mid is not None})
    if not unique_ids:
        return {}

    ModeratorProfile = aliased(Profile)
    stmt = (
        select(User, ModeratorProfile)
        .outerjoin(ModeratorProfile, ModeratorProfile.user_id == User.id)
        .where(User.id.in_(unique_ids))
    )
    rows = (await db.execute(stmt)).all()
    return {
        user.id: _resolve_moderator_name(user, profile)
        for user, profile in rows
    }


async def get_reported_entities(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    status: ReportStatus | None = None,
    moderator_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    viewer_user_id: UUID,
) -> ReportedEntityListResponse:
    """
    Return one moderation-dashboard row per reported entity.

    Each item includes the full entity payload plus report metadata.
    Optionally filter by report status (under_review, actioned, rejected).
    """
    _validate_entity_type(entity_type)
    offset = (page - 1) * page_size

    rows = await fetch_reported_entity_rows(
        db,
        entity_type=entity_type,
        status=status,
        moderator_id=moderator_id,
        offset=offset,
        limit=page_size,
    )
    total_items = await count_reported_entities(
        db,
        entity_type=entity_type,
        status=status,
        moderator_id=moderator_id,
    )

    entity_ids = [row["entity_id"] for row in rows]
    entities = await _load_entities_for_queue(
        db,
        entity_type=entity_type,
        entity_ids=entity_ids,
        viewer_user_id=viewer_user_id,
    )
    moderator_names = await _batch_moderator_names(
        db,
        [row["moderator_id"] for row in rows],
    )
    previous_rows = await get_previous_report_comments_for_entities(
        db,
        entity_type=entity_type,
        entity_ids=entity_ids,
        exclude_report_ids=[row["report_id"] for row in rows if row.get("report_id")],
    )
    previous_by_entity: dict[UUID, list[tuple[Report, User | None, Profile | None]]] = {}
    for previous_row in previous_rows:
        report = previous_row[0]
        previous_by_entity.setdefault(report.entity_id, []).append(previous_row)

    items = [
        ReportedEntityItem(
            entity=entities.get(row["entity_id"]),
            report_count=row["report_count"],
            latest_reported_at=row["latest_reported_at"],
            moderator_id=row["moderator_id"],
            moderator_name=(
                moderator_names.get(row["moderator_id"])
                if row["moderator_id"] is not None
                else None
            ),
            status=row["status"],
            admin_comment=row.get("admin_comment"),
            is_reviewed=is_report_reviewed(row["status"]),
            previous_comments=_format_previous_comments(
                previous_by_entity.get(row["entity_id"], [])
            ),
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
    previous_rows = await get_previous_report_comments(
        db,
        entity_type=report.entity_type,
        entity_id=report.entity_id,
        exclude_report_id=report.id,
    )
    detail = format_report_detail(
        row[0],
        row[1],
        row[2],
        row[3],
        row[4],
        report_count=report_counts.get((report.entity_type, report.entity_id), 0),
        previous_comments=_format_previous_comments(previous_rows),
    )
    return success_response(
        message="Report details retrieved successfully",
        data=detail,
        response_cls=ReportResponse,
    )


async def _apply_actioned_report_to_entity(
    db: AsyncSession,
    report: Report,
    *,
    moderator_id: UUID,
) -> str | None:
    """
    Apply side effects when a report is actioned.

    - post: set state to flagged
    - comment: soft-delete (is_deleted=True)
    - user: set status to suspended (and disable Firebase account when present)
    - rejected reviews leave the entity unchanged (caller skips this)

    Returns an error message if the entity cannot be updated, else None.
    """
    from datetime import datetime, timezone

    if report.entity_type == ReportEntityType.post:
        post = (
            await db.execute(select(Post).where(Post.id == report.entity_id))
        ).scalar_one_or_none()
        if post is None:
            return "Reported post not found"
        post.state = PostState.flagged
        post.moderator_id = moderator_id
        post.is_moderator_reviewed = True
        post.reviewed_at = datetime.now(timezone.utc)
        post.updated_at = datetime.now(timezone.utc)
        db.add(post)
        return None

    if report.entity_type == ReportEntityType.comment:
        comment = await get_comment_by_id(db, report.entity_id)
        if comment is None:
            return "Reported comment not found"
        await mark_comment_deleted(db, comment)
        return None

    if report.entity_type == ReportEntityType.user:
        user = (
            await db.execute(select(User).where(User.id == report.entity_id))
        ).scalar_one_or_none()
        if user is None:
            return "Reported user not found"
        if user.is_deleted or user.deleted_at is not None or user.status == UserStatus.deleting:
            return "Cannot action a soft-deleted user"

        user.status = UserStatus.suspended
        user.updated_at = datetime.now(timezone.utc)
        db.add(user)

        if user.firebase_uid:
            try:
                from core.auth.services import disable_firebase_user

                disable_firebase_user(user.firebase_uid)
            except Exception:
                logger.exception(
                    "Failed to disable Firebase user for actioned report user_id=%s",
                    user.id,
                )
        return None

    return None


async def review_report_admin_service(
    db: AsyncSession,
    current_admin_id: UUID,
    payload: ReportReviewRequest,
) -> ReportResponse:
    report_id = payload.report_id
    row = await get_report_by_id(db, report_id)
    if row is None:
        return error_response("Report not found", response_cls=ReportResponse)

    report = row[0]

    try:
        if payload.status == ReportStatus.actioned:
            action_error = await _apply_actioned_report_to_entity(
                db,
                report,
                moderator_id=current_admin_id,
            )
            if action_error:
                await db.rollback()
                return error_response(action_error, response_cls=ReportResponse)

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
    previous_rows = await get_previous_report_comments(
        db,
        entity_type=report.entity_type,
        entity_id=report.entity_id,
        exclude_report_id=report.id,
    )
    detail = format_report_detail(
        updated_row[0],
        updated_row[1],
        updated_row[2],
        updated_row[3],
        updated_row[4],
        report_count=report_counts.get((report.entity_type, report.entity_id), 0),
        previous_comments=_format_previous_comments(previous_rows),
    )
    return success_response(
        message="Report reviewed successfully",
        data=detail,
        response_cls=ReportResponse,
    )
