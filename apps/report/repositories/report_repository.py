from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.accounts.db_models import User
from apps.report.db_models import Report
from apps.profiles.db_models import Profile
from common.enums import ReportEntityType, ReportStatus


async def create_report(
    db: AsyncSession,
    reported_id: UUID,
    entity_type: ReportEntityType,
    entity_id: UUID,
    reason: str,
    moderator_id: UUID | None = None,
) -> Report:
    report = Report(
        reported_id=reported_id,
        entity_type=entity_type,
        entity_id=entity_id,
        reason=reason,
        status=ReportStatus.under_review,
        moderator_id=moderator_id,
    )
    db.add(report)
    return report


async def get_report_by_id(
    db: AsyncSession,
    report_id: UUID,
) -> tuple[Report, User, Profile | None, User | None, Profile | None] | None:
    reporter_user = aliased(User, name="reporter_user")
    reporter_profile = aliased(Profile, name="reporter_profile")
    moderator_user = aliased(User, name="moderator_user")
    moderator_profile = aliased(Profile, name="moderator_profile")

    stmt = (
        select(Report, reporter_user, reporter_profile, moderator_user, moderator_profile)
        .join(reporter_user, reporter_user.id == Report.reported_id)
        .outerjoin(reporter_profile, reporter_profile.user_id == Report.reported_id)
        .outerjoin(moderator_user, moderator_user.id == Report.moderator_id)
        .outerjoin(moderator_profile, moderator_profile.user_id == Report.moderator_id)
        .where(Report.id == report_id)
    )
    result = (await db.execute(stmt)).first()
    return result


async def get_duplicate_report(
    db: AsyncSession,
    reported_id: UUID,
    entity_type: ReportEntityType,
    entity_id: UUID,
) -> Report | None:
    stmt = select(Report).where(
        Report.reported_id == reported_id,
        Report.entity_type == entity_type,
        Report.entity_id == entity_id,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_previous_report_comments(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    entity_id: UUID,
    exclude_report_id: UUID,
) -> list[tuple[Report, User | None, Profile | None]]:
    """
    Fetch prior reviewed moderation comments for the same entity.

    Includes reports that already have an admin_comment, excluding the current
    report. Ordered by updated_at ASC for chronological history.
    """
    moderator_user = aliased(User, name="previous_moderator_user")
    moderator_profile = aliased(Profile, name="previous_moderator_profile")

    stmt = (
        select(Report, moderator_user, moderator_profile)
        .outerjoin(moderator_user, moderator_user.id == Report.moderator_id)
        .outerjoin(moderator_profile, moderator_profile.user_id == Report.moderator_id)
        .where(
            Report.entity_type == entity_type,
            Report.entity_id == entity_id,
            Report.id != exclude_report_id,
            Report.admin_comment.is_not(None),
            Report.admin_comment != "",
            Report.status.in_((ReportStatus.rejected, ReportStatus.actioned)),
        )
        .order_by(Report.updated_at.asc(), Report.created_at.asc())
    )
    return list((await db.execute(stmt)).all())


async def get_previous_report_comments_for_entities(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    entity_ids: list[UUID],
    exclude_report_ids: list[UUID],
) -> list[tuple[Report, User | None, Profile | None]]:
    """
    Batch-fetch prior reviewed moderation comments for many entities.

    Excludes the latest/current report ids shown on the queue rows. Ordered by
    entity_id, then updated_at ASC for chronological history per entity.
    """
    if not entity_ids:
        return []

    moderator_user = aliased(User, name="batch_previous_moderator_user")
    moderator_profile = aliased(Profile, name="batch_previous_moderator_profile")

    conditions = [
        Report.entity_type == entity_type,
        Report.entity_id.in_(entity_ids),
        Report.admin_comment.is_not(None),
        Report.admin_comment != "",
        Report.status.in_((ReportStatus.rejected, ReportStatus.actioned)),
    ]
    if exclude_report_ids:
        conditions.append(Report.id.notin_(exclude_report_ids))

    stmt = (
        select(Report, moderator_user, moderator_profile)
        .outerjoin(moderator_user, moderator_user.id == Report.moderator_id)
        .outerjoin(moderator_profile, moderator_profile.user_id == Report.moderator_id)
        .where(*conditions)
        .order_by(
            Report.entity_id.asc(),
            Report.updated_at.asc(),
            Report.created_at.asc(),
        )
    )
    return list((await db.execute(stmt)).all())


async def get_reports(
    db: AsyncSession,
    *,
    status: ReportStatus | None = None,
    entity_type: ReportEntityType | None = None,
    entity_id: UUID | None = None,
    moderator_id: UUID | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple[Report, User, Profile | None, User | None, Profile | None]]:
    """Fetch individual report rows with reporter and moderator joins."""
    reporter_user = aliased(User, name="reporter_user")
    reporter_profile = aliased(Profile, name="reporter_profile")
    moderator_user = aliased(User, name="moderator_user")
    moderator_profile = aliased(Profile, name="moderator_profile")

    conditions = _report_filter_conditions(
        status=status,
        entity_type=entity_type,
        entity_id=entity_id,
        moderator_id=moderator_id,
    )

    stmt = (
        select(Report, reporter_user, reporter_profile, moderator_user, moderator_profile)
        .join(reporter_user, reporter_user.id == Report.reported_id)
        .outerjoin(reporter_profile, reporter_profile.user_id == Report.reported_id)
        .outerjoin(moderator_user, moderator_user.id == Report.moderator_id)
        .outerjoin(moderator_profile, moderator_profile.user_id == Report.moderator_id)
        .where(*conditions)
        .order_by(Report.created_at.desc(), Report.id.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    return list((await db.execute(stmt)).all())


async def count_reports(
    db: AsyncSession,
    *,
    status: ReportStatus | None = None,
    entity_type: ReportEntityType | None = None,
    entity_id: UUID | None = None,
    moderator_id: UUID | None = None,
) -> int:
    conditions = _report_filter_conditions(
        status=status,
        entity_type=entity_type,
        entity_id=entity_id,
        moderator_id=moderator_id,
    )
    stmt = select(func.count(Report.id)).where(*conditions)
    return int((await db.execute(stmt)).scalar_one())


async def sync_open_report_moderator_for_post(
    db: AsyncSession,
    *,
    post_id: UUID,
    moderator_id: UUID,
) -> None:
    """Reassign open reports when a post's moderator changes (e.g. deleted moderator → Super Admin).

    Updates under_review reports for the post itself and for comments on that post.
    """
    from apps.engagement.db_models import Comment

    comment_ids_stmt = select(Comment.id).where(Comment.post_id == post_id)
    now = datetime.now(timezone.utc)
    stmt = (
        update(Report)
        .where(
            Report.status == ReportStatus.under_review,
            or_(
                and_(
                    Report.entity_type == ReportEntityType.post,
                    Report.entity_id == post_id,
                ),
                and_(
                    Report.entity_type == ReportEntityType.comment,
                    Report.entity_id.in_(comment_ids_stmt),
                ),
            ),
        )
        .values(moderator_id=moderator_id, updated_at=now)
    )
    await db.execute(stmt)


async def update_report(
    db: AsyncSession,
    report_id: UUID,
    status: ReportStatus,
    admin_comment: str | None,
    moderator_id: UUID,
) -> Report | None:
    stmt = select(Report).where(Report.id == report_id)
    report = (await db.execute(stmt)).scalar_one_or_none()
    if report is None:
        return None

    report.status = status
    report.admin_comment = admin_comment
    report.moderator_id = moderator_id
    report.updated_at = datetime.now(timezone.utc)
    db.add(report)
    return report


async def count_reports_for_entity(
    db: AsyncSession,
    entity_type: ReportEntityType,
    entity_id: UUID,
) -> int:
    stmt = select(func.count(Report.id)).where(
        Report.entity_type == entity_type,
        Report.entity_id == entity_id,
    )
    return int((await db.execute(stmt)).scalar_one())


async def count_reports_by_entity_ids(
    db: AsyncSession,
    entity_type: ReportEntityType,
    entity_ids: list[UUID],
) -> dict[UUID, int]:
    """Return report counts keyed by entity_id for a single entity type."""
    if not entity_ids:
        return {}
    stmt = (
        select(Report.entity_id, func.count(Report.id))
        .where(
            Report.entity_type == entity_type,
            Report.entity_id.in_(entity_ids),
        )
        .group_by(Report.entity_id)
    )
    rows = (await db.execute(stmt)).all()
    return {entity_id: int(count) for entity_id, count in rows}


async def count_reports_by_entity_keys(
    db: AsyncSession,
    keys: list[tuple[ReportEntityType, UUID]],
) -> dict[tuple[ReportEntityType, UUID], int]:
    """Return report counts keyed by (entity_type, entity_id)."""
    if not keys:
        return {}

    counts: dict[tuple[ReportEntityType, UUID], int] = {}
    by_type: dict[ReportEntityType, list[UUID]] = {}
    for entity_type, entity_id in keys:
        by_type.setdefault(entity_type, []).append(entity_id)

    for entity_type, entity_ids in by_type.items():
        type_counts = await count_reports_by_entity_ids(db, entity_type, entity_ids)
        for entity_id, count in type_counts.items():
            counts[(entity_type, entity_id)] = count
    return counts


def _report_filter_conditions(
    *,
    status: ReportStatus | None = None,
    entity_type: ReportEntityType | None = None,
    entity_id: UUID | None = None,
    moderator_id: UUID | None = None,
) -> list:
    conditions = []
    if status is not None:
        conditions.append(Report.status == status)
    if entity_type is not None:
        conditions.append(Report.entity_type == entity_type)
    if entity_id is not None:
        conditions.append(Report.entity_id == entity_id)
    if moderator_id is not None:
        conditions.append(Report.moderator_id == moderator_id)
    return conditions


async def get_reported_entities(
    db: AsyncSession,
    *,
    status: ReportStatus | None = None,
    entity_type: ReportEntityType | None = None,
    moderator_id: UUID | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> list[dict]:
    """
    Return one moderation-queue row per (entity_type, entity_id).

    Aggregates report_count / timestamps and takes status + moderator_id from
    the latest report in the filtered set (created_at DESC, id DESC).
    """
    conditions = _report_filter_conditions(
        status=status,
        entity_type=entity_type,
        moderator_id=moderator_id,
    )

    ranked = (
        select(
            Report.id.label("report_id"),
            Report.entity_type.label("entity_type"),
            Report.entity_id.label("entity_id"),
            Report.status.label("status"),
            Report.moderator_id.label("moderator_id"),
            Report.admin_comment.label("admin_comment"),
            Report.created_at.label("latest_reported_at"),
            func.count(Report.id)
            .over(partition_by=(Report.entity_type, Report.entity_id))
            .label("report_count"),
            func.min(Report.created_at)
            .over(partition_by=(Report.entity_type, Report.entity_id))
            .label("created_at"),
            func.max(Report.updated_at)
            .over(partition_by=(Report.entity_type, Report.entity_id))
            .label("updated_at"),
            func.row_number()
            .over(
                partition_by=(Report.entity_type, Report.entity_id),
                order_by=(Report.created_at.desc(), Report.id.desc()),
            )
            .label("rn"),
        )
        .where(*conditions)
        .subquery()
    )

    stmt = (
        select(
            ranked.c.report_id,
            ranked.c.entity_type,
            ranked.c.entity_id,
            ranked.c.report_count,
            ranked.c.status,
            ranked.c.moderator_id,
            ranked.c.admin_comment,
            ranked.c.latest_reported_at,
            ranked.c.created_at,
            ranked.c.updated_at,
        )
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.latest_reported_at.desc(), ranked.c.entity_id.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).all()
    return [
        {
            "report_id": row.report_id,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "report_count": int(row.report_count),
            "status": row.status,
            "moderator_id": row.moderator_id,
            "admin_comment": row.admin_comment,
            "latest_reported_at": row.latest_reported_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


async def count_reported_entities(
    db: AsyncSession,
    *,
    status: ReportStatus | None = None,
    entity_type: ReportEntityType | None = None,
    moderator_id: UUID | None = None,
) -> int:
    """Count distinct reported entities matching the given filters."""
    conditions = _report_filter_conditions(
        status=status,
        entity_type=entity_type,
        moderator_id=moderator_id,
    )
    grouped = (
        select(Report.entity_type, Report.entity_id)
        .where(*conditions)
        .group_by(Report.entity_type, Report.entity_id)
        .subquery()
    )
    stmt = select(func.count()).select_from(grouped)
    return int((await db.execute(stmt)).scalar_one())
