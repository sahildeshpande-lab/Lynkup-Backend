from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.administration.db_models.admin_activity_log_db_model import AdminActivityLog
from apps.profiles.db_models import Profile
from common.enums import AdminActivityLogOrder, AdminActivityLogRole, AdminActivityLogSort
from common.time import utc_now

HIDDEN_ADMIN_ACTIVITY_LOG_MODULES: frozenset[str] = frozenset({"profanity_words"})
PROFANITY_WORDS_ACTIVITY_MODULE = "profanity_words"


def _exclude_hidden_modules():
    return func.lower(AdminActivityLog.module).notin_(
        tuple(HIDDEN_ADMIN_ACTIVITY_LOG_MODULES)
    )


async def create_admin_activity_log_record(
    db: AsyncSession,
    *,
    user_id: UUID,
    role: str,
    action: str,
    module: str,
    record_id: UUID | None = None,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AdminActivityLog:
    record = AdminActivityLog(
        user_id=user_id,
        role=role,
        action=action,
        module=module,
        record_id=record_id,
        description=description,
        log_metadata=metadata,
    )
    db.add(record)
    await db.flush()
    return record


def _user_name_expr():
    """SQL equivalent of response ``user_name`` / ``_staff_display_name``."""
    full_name = func.nullif(
        func.trim(func.concat_ws(" ", Profile.first_name, Profile.last_name)),
        "",
    )
    return func.coalesce(full_name, User.email)


def _search_filters(search: str):
    term = f"%{search.strip()}%"
    return or_(
        _user_name_expr().ilike(term),
        AdminActivityLog.description.ilike(term),
    )


def _activity_logs_order_by(
    sort: AdminActivityLogSort | None = None,
    order: AdminActivityLogOrder | None = None,
):
    """ORDER BY for GET /admin/activity-logs.

    ``created_at`` (default): newest first when ``order=desc``.
    ``module`` / ``action``: alphabetical, then created_at as a tiebreaker.
    ``updated_at``: last log update.
    """
    descending = order != AdminActivityLogOrder.asc
    sort_column = {
        AdminActivityLogSort.module: AdminActivityLog.module,
        AdminActivityLogSort.action: AdminActivityLog.action,
        AdminActivityLogSort.updated_at: AdminActivityLog.updated_at,
    }.get(sort, AdminActivityLog.created_at)

    if descending:
        primary = sort_column.desc()
        created_at = AdminActivityLog.created_at.desc()
        tiebreaker = AdminActivityLog.id.desc()
    else:
        primary = sort_column.asc()
        created_at = AdminActivityLog.created_at.asc()
        tiebreaker = AdminActivityLog.id.asc()

    if sort_column is AdminActivityLog.created_at:
        return (primary, tiebreaker)
    return (primary, created_at, tiebreaker)


def _staff_display_name(
    first_name: str | None,
    last_name: str | None,
    email: str | None,
) -> str | None:
    parts = [
        part.strip()
        for part in (first_name, last_name)
        if isinstance(part, str) and part.strip()
    ]
    if parts:
        return " ".join(parts)
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def _moderator_assignment_filter(moderator_id: UUID):
    from apps.feed.db_models.post_db_model import Post
    from apps.moderation.db_models.moderation_history_db_model import ModerationHistory
    from apps.report.db_models.report_db_model import Report
    from common.enums import ReportEntityType

    post_ids_by_mod = select(Post.id).where(Post.moderator_id == moderator_id)
    post_ids_by_rep = select(Report.entity_id).where(
        Report.entity_type == ReportEntityType.post,
        Report.moderator_id == moderator_id,
    )
    post_ids_by_hist = select(ModerationHistory.entity_id).where(
        ModerationHistory.entity_type == ReportEntityType.post,
        ModerationHistory.moderator_id == moderator_id,
    )

    report_ids_by_mod = select(Report.id).where(Report.moderator_id == moderator_id)
    report_ids_by_entity_post = select(Report.id).where(
        Report.entity_type == ReportEntityType.post,
        Report.entity_id.in_(post_ids_by_mod),
    )
    report_ids_by_hist = select(Report.id).where(
        Report.entity_id.in_(
            select(ModerationHistory.entity_id).where(ModerationHistory.moderator_id == moderator_id)
        )
    )

    comment_ids_by_rep = select(Report.entity_id).where(
        Report.entity_type == ReportEntityType.comment,
        Report.moderator_id == moderator_id,
    )
    comment_ids_by_hist = select(ModerationHistory.entity_id).where(
        ModerationHistory.entity_type == ReportEntityType.comment,
        ModerationHistory.moderator_id == moderator_id,
    )

    user_ids_by_rep = select(Report.entity_id).where(
        Report.entity_type == ReportEntityType.user,
        Report.moderator_id == moderator_id,
    )
    user_ids_by_hist = select(ModerationHistory.entity_id).where(
        ModerationHistory.entity_type == ReportEntityType.user,
        ModerationHistory.moderator_id == moderator_id,
    )

    superadmin_post_match = (
        (func.lower(AdminActivityLog.role) == "superadmin")
        & (func.lower(AdminActivityLog.module) == "post")
        & (
            AdminActivityLog.record_id.in_(post_ids_by_mod)
            | AdminActivityLog.record_id.in_(post_ids_by_rep)
            | AdminActivityLog.record_id.in_(post_ids_by_hist)
        )
    )

    superadmin_report_match = (
        (func.lower(AdminActivityLog.role) == "superadmin")
        & (func.lower(AdminActivityLog.module) == "report")
        & (
            AdminActivityLog.record_id.in_(report_ids_by_mod)
            | AdminActivityLog.record_id.in_(report_ids_by_entity_post)
            | AdminActivityLog.record_id.in_(report_ids_by_hist)
        )
    )

    superadmin_comment_match = (
        (func.lower(AdminActivityLog.role) == "superadmin")
        & (func.lower(AdminActivityLog.module) == "comment")
        & (
            AdminActivityLog.record_id.in_(comment_ids_by_rep)
            | AdminActivityLog.record_id.in_(comment_ids_by_hist)
        )
    )

    superadmin_user_match = (
        (func.lower(AdminActivityLog.role) == "superadmin")
        & (func.lower(AdminActivityLog.module) == "user")
        & (
            AdminActivityLog.record_id.in_(user_ids_by_rep)
            | AdminActivityLog.record_id.in_(user_ids_by_hist)
        )
    )

    return or_(
        AdminActivityLog.user_id == moderator_id,
        superadmin_post_match,
        superadmin_report_match,
        superadmin_comment_match,
        superadmin_user_match,
    )


async def list_admin_activity_logs(
    db: AsyncSession,
    *,
    module: str | None = None,
    role: AdminActivityLogRole | str | None = None,
    moderator_id: UUID | None = None,
    search: str | None = None,
    sort: AdminActivityLogSort | None = None,
    order: AdminActivityLogOrder | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> tuple[list[tuple[AdminActivityLog, str | None]], int]:
    filters = [_exclude_hidden_modules()]
    module_value = (module or "").strip()
    if module_value:
        filters.append(func.lower(AdminActivityLog.module) == module_value.lower())
    role_value = role.value if isinstance(role, AdminActivityLogRole) else str(role or "").strip().lower()
    if role_value in {item.value for item in AdminActivityLogRole}:
        filters.append(func.lower(AdminActivityLog.role) == role_value)
    if moderator_id is not None:
        filters.append(_moderator_assignment_filter(moderator_id))
    if search and search.strip():
        filters.append(_search_filters(search))

    count_stmt = select(func.count()).select_from(AdminActivityLog)
    if search and search.strip():
        count_stmt = (
            count_stmt.outerjoin(User, User.id == AdminActivityLog.user_id).outerjoin(
                Profile, Profile.user_id == AdminActivityLog.user_id
            )
        )
    if filters:
        count_stmt = count_stmt.where(*filters)
    total_items = int((await db.execute(count_stmt)).scalar_one() or 0)

    stmt = (
        select(AdminActivityLog, Profile.first_name, Profile.last_name, User.email)
        .select_from(AdminActivityLog)
        .outerjoin(User, User.id == AdminActivityLog.user_id)
        .outerjoin(Profile, Profile.user_id == AdminActivityLog.user_id)
    )
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.order_by(*_activity_logs_order_by(sort, order))
    if limit is not None:
        stmt = stmt.offset(max(offset, 0)).limit(limit)

    fetched = list((await db.execute(stmt)).all())
    rows = [
        (log, _staff_display_name(first_name, last_name, email))
        for log, first_name, last_name, email in fetched
    ]
    return rows, total_items


async def get_admin_activity_log_by_id(
    db: AsyncSession,
    log_id: UUID,
) -> tuple[AdminActivityLog, str | None] | None:
    stmt = (
        select(AdminActivityLog, Profile.first_name, Profile.last_name, User.email)
        .select_from(AdminActivityLog)
        .outerjoin(User, User.id == AdminActivityLog.user_id)
        .outerjoin(Profile, Profile.user_id == AdminActivityLog.user_id)
        .where(AdminActivityLog.id == log_id)
    )
    fetched = (await db.execute(stmt)).first()
    if fetched is None:
        return None
    log, first_name, last_name, email = fetched
    return log, _staff_display_name(first_name, last_name, email)


async def list_admin_notification_activity_logs(
    db: AsyncSession,
    *,
    moderator_id: UUID | None = None,
    is_read: bool | None = None,
    created_after: datetime | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> tuple[list[tuple[AdminActivityLog, str | None]], int]:
    filters = [_exclude_hidden_modules()]
    if moderator_id is not None:
        filters.append(_moderator_assignment_filter(moderator_id))
    if is_read is not None:
        filters.append(AdminActivityLog.is_read.is_(is_read))
    if created_after is not None:
        filters.append(AdminActivityLog.created_at >= created_after)

    count_stmt = select(func.count(func.distinct(AdminActivityLog.id))).select_from(AdminActivityLog)
    if filters:
        count_stmt = count_stmt.where(*filters)
    total_items = int((await db.execute(count_stmt)).scalar_one() or 0)

    stmt = (
        select(AdminActivityLog, Profile.first_name, Profile.last_name, User.email)
        .select_from(AdminActivityLog)
        .outerjoin(User, User.id == AdminActivityLog.user_id)
        .outerjoin(Profile, Profile.user_id == AdminActivityLog.user_id)
    )
    if filters:
        stmt = stmt.where(*filters)
    stmt = stmt.distinct().order_by(AdminActivityLog.created_at.desc(), AdminActivityLog.id.desc())
    if limit is not None:
        stmt = stmt.offset(max(offset, 0)).limit(limit)

    fetched = list((await db.execute(stmt)).all())
    seen_ids: set[UUID] = set()
    rows = []
    for log, first_name, last_name, email in fetched:
        if log.id in seen_ids:
            continue
        seen_ids.add(log.id)
        rows.append((log, _staff_display_name(first_name, last_name, email)))
    return rows, total_items


async def mark_admin_activity_log_read(
    db: AsyncSession,
    log: AdminActivityLog,
) -> AdminActivityLog:
    now = utc_now()
    log.is_read = True
    log.read_at = now
    log.updated_at = now
    db.add(log)
    await db.flush()
    await db.refresh(log)
    return log


async def mark_all_admin_activity_logs_read(
    db: AsyncSession,
    *,
    moderator_id: UUID | None = None,
    created_after: datetime | None = None,
) -> int:
    now = utc_now()
    filters = [_exclude_hidden_modules(), AdminActivityLog.is_read.is_(False)]
    if moderator_id is not None:
        filters.append(_moderator_assignment_filter(moderator_id))
    if created_after is not None:
        filters.append(AdminActivityLog.created_at >= created_after)

    stmt = (
        update(AdminActivityLog)
        .where(*filters)
        .values(is_read=True, read_at=now, updated_at=now)
    )
    result = await db.execute(stmt)
    await db.flush()
    return int(result.rowcount or 0)


async def count_unread_admin_notification_activity_logs(
    db: AsyncSession,
    *,
    moderator_id: UUID | None = None,
    created_after: datetime | None = None,
) -> int:
    filters = [_exclude_hidden_modules(), AdminActivityLog.is_read.is_(False)]
    if moderator_id is not None:
        filters.append(_moderator_assignment_filter(moderator_id))
    if created_after is not None:
        filters.append(AdminActivityLog.created_at >= created_after)

    count_stmt = select(func.count()).select_from(AdminActivityLog).where(*filters)
    return int((await db.execute(count_stmt)).scalar_one() or 0)

