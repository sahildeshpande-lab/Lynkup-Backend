from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.accounts.db_models import User
from apps.engagement.db_models import Report
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


async def get_reports(
    db: AsyncSession,
    *,
    status: ReportStatus | None = None,
    entity_type: ReportEntityType | None = None,
    moderator_id: UUID | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple[Report, User, Profile | None, User | None, Profile | None]]:
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
    )

    if status is not None:
        stmt = stmt.where(Report.status == status)
    if entity_type is not None:
        stmt = stmt.where(Report.entity_type == entity_type)
    if moderator_id is not None:
        stmt = stmt.where(Report.moderator_id == moderator_id)

    stmt = stmt.order_by(Report.created_at.desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)

    return list((await db.execute(stmt)).all())


async def count_reports(
    db: AsyncSession,
    *,
    status: ReportStatus | None = None,
    entity_type: ReportEntityType | None = None,
    moderator_id: UUID | None = None,
) -> int:
    stmt = select(func.count(Report.id))

    if status is not None:
        stmt = stmt.where(Report.status == status)
    if entity_type is not None:
        stmt = stmt.where(Report.entity_type == entity_type)
    if moderator_id is not None:
        stmt = stmt.where(Report.moderator_id == moderator_id)

    return int((await db.execute(stmt)).scalar_one())


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
