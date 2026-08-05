from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.moderation.db_models import ModerationHistory
from common.enums import ReportEntityType


async def create_history(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    entity_id: UUID,
    action: str,
    moderator_id: UUID | None = None,
    comment: str | None = None,
) -> ModerationHistory:
    """Insert a new immutable moderation history row."""
    history = ModerationHistory(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        moderator_id=moderator_id,
        comment=comment,
    )
    db.add(history)
    await db.flush()
    return history


async def get_history(
    db: AsyncSession,
    entity_type: ReportEntityType,
    entity_id: UUID,
) -> list[ModerationHistory]:
    """Return moderation history for an entity, newest first."""
    stmt = (
        select(ModerationHistory)
        .where(
            ModerationHistory.entity_type == entity_type,
            ModerationHistory.entity_id == entity_id,
        )
        .order_by(ModerationHistory.created_at.desc(), ModerationHistory.id.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_latest(
    db: AsyncSession,
    entity_type: ReportEntityType,
    entity_id: UUID,
) -> ModerationHistory | None:
    """Return the most recent moderation event for an entity, if any."""
    stmt = (
        select(ModerationHistory)
        .where(
            ModerationHistory.entity_type == entity_type,
            ModerationHistory.entity_id == entity_id,
        )
        .order_by(ModerationHistory.created_at.desc(), ModerationHistory.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
