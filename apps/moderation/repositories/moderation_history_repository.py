from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from apps.accounts.db_models import User
from apps.moderation.db_models import ModerationHistory
from apps.profiles.db_models import Profile
from common.enums import ReportEntityType
from common.exceptions import ApiError

_HISTORY_ENTITY_TYPES = (ReportEntityType.post, ReportEntityType.user)


async def create_history(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    entity_id: UUID,
    action: str,
    moderator_id: UUID | None = None,
    comment: str | None = None,
) -> ModerationHistory:
    """Insert a new immutable moderation history row (post/user only)."""
    if entity_type not in _HISTORY_ENTITY_TYPES:
        raise ApiError("Moderation history only supports post and user entities")

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


async def get_history_by_entity_id(
    db: AsyncSession,
    entity_id: UUID,
) -> list[tuple[ModerationHistory, User | None, Profile | None]]:
    """Return post/user moderation history for an entity_id, newest first."""
    ModeratorProfile = aliased(Profile)
    stmt = (
        select(ModerationHistory, User, ModeratorProfile)
        .outerjoin(User, User.id == ModerationHistory.moderator_id)
        .outerjoin(ModeratorProfile, ModeratorProfile.user_id == User.id)
        .where(
            ModerationHistory.entity_id == entity_id,
            ModerationHistory.entity_type.in_(_HISTORY_ENTITY_TYPES),
        )
        .order_by(ModerationHistory.created_at.desc(), ModerationHistory.id.desc())
    )
    return list((await db.execute(stmt)).all())


async def get_history(
    db: AsyncSession,
    entity_type: ReportEntityType,
    entity_id: UUID,
) -> list[ModerationHistory]:
    """Return moderation history for an entity type + id, newest first."""
    if entity_type not in _HISTORY_ENTITY_TYPES:
        return []
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
    if entity_type not in _HISTORY_ENTITY_TYPES:
        return None
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
