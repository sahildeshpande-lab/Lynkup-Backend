from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import Role, User, UserRole
from apps.moderation.db_models import ModerationAssignmentState
from apps.moderation.db_models.moderation_assignment_state_db_model import (
    MODERATION_ASSIGNMENT_STATE_ID,
    utc_now,
)
from common.enums import UserStatus
from common.exceptions import ApiError


def pick_next_moderator(
    moderator_ids: list[UUID],
    last_assigned_moderator_id: UUID | None,
) -> UUID:
    if not moderator_ids:
        raise ApiError("No active moderators available for post assignment")
    if len(moderator_ids) == 1:
        return moderator_ids[0]
    if (
        last_assigned_moderator_id is None
        or last_assigned_moderator_id not in moderator_ids
    ):
        return moderator_ids[0]
    current_index = moderator_ids.index(last_assigned_moderator_id)
    return moderator_ids[(current_index + 1) % len(moderator_ids)]


async def _fetch_active_moderator_ids(db: AsyncSession) -> list[UUID]:
    stmt = (
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            Role.name == "moderator",
            User.is_deleted.is_(False),
            User.deleted_at.is_(None),
            User.status == UserStatus.active,
        )
        .order_by(User.created_at.asc(), User.id.asc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _get_or_create_assignment_state_for_update(
    db: AsyncSession,
) -> ModerationAssignmentState:
    stmt = (
        select(ModerationAssignmentState)
        .where(ModerationAssignmentState.id == MODERATION_ASSIGNMENT_STATE_ID)
        .with_for_update()
    )
    result = await db.execute(stmt)
    state = result.scalar_one_or_none()
    if state is not None:
        return state

    state = ModerationAssignmentState(
        id=MODERATION_ASSIGNMENT_STATE_ID,
        last_assigned_moderator_id=None,
    )
    db.add(state)
    await db.flush()
    return state


async def assign_next_moderator_round_robin(db: AsyncSession) -> UUID:
    """
    Select the next moderator using round-robin.

    Must be called inside an open transaction. Locks the singleton
    ``moderation_assignment_state`` row with ``SELECT ... FOR UPDATE``.
    """
    moderator_ids = await _fetch_active_moderator_ids(db)
    if not moderator_ids:
        raise ApiError("No active moderators available for post assignment")

    state = await _get_or_create_assignment_state_for_update(db)
    next_moderator_id = pick_next_moderator(
        moderator_ids,
        state.last_assigned_moderator_id,
    )
    state.last_assigned_moderator_id = next_moderator_id
    state.updated_at = utc_now()
    db.add(state)
    return next_moderator_id
