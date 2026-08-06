from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.moderation.repositories import create_history, get_history_by_entity_id
from common.enums import ReportEntityType
from common.exceptions import ApiError

_HISTORY_ENTITY_TYPES = frozenset({ReportEntityType.post, ReportEntityType.user})


def _resolve_moderator_name(profile=None, user=None) -> str | None:
    if profile is not None:
        parts = [
            part
            for part in (getattr(profile, "first_name", None), getattr(profile, "last_name", None))
            if part
        ]
        name = " ".join(parts).strip()
        if name:
            return name
    if user is not None:
        email = getattr(user, "email", None)
        if email:
            return email
    return None


def _format_history_item(history, moderator_user=None, moderator_profile=None) -> dict:
    return {
        "id": history.id,
        "entity_id": history.entity_id,
        "action_taken": history.action,
        "moderator_name": _resolve_moderator_name(moderator_profile, moderator_user),
        "comment": history.comment,
        "action_taken_at": history.created_at,
    }


async def record_moderation_history(
    db: AsyncSession,
    *,
    entity_type: ReportEntityType,
    entity_id: UUID,
    action: str,
    moderator_id: UUID | None = None,
    comment: str | None = None,
):
    """Persist a moderation event for post or user entities only."""
    if entity_type not in _HISTORY_ENTITY_TYPES:
        raise ApiError("Moderation history only supports post and user entities")
    return await create_history(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        moderator_id=moderator_id,
        comment=comment,
    )


async def list_moderation_history_service(
    db: AsyncSession,
    entity_id: UUID,
) -> list[dict]:
    """Return moderation history rows for an entity_id (post or user)."""
    rows = await get_history_by_entity_id(db, entity_id)
    return [
        _format_history_item(history, moderator_user, moderator_profile)
        for history, moderator_user, moderator_profile in rows
    ]
