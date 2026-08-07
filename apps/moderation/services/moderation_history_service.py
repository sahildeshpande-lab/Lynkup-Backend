from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.moderation.repositories import create_history, get_history_by_entity_id
from common.enums import ReportEntityType
from common.exceptions import ApiError

_HISTORY_ENTITY_TYPES = frozenset({ReportEntityType.post, ReportEntityType.user})

_PROCESSING_COMMENT = "Author edited after moderator review."


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


def _format_processing_event(revision) -> dict:
    """Synthetic timeline row — not persisted in moderation_history."""
    return {
        "id": None,
        "entity_id": revision.post_id,
        "action_taken": "processing",
        "moderator_name": None,
        "comment": _PROCESSING_COMMENT,
        "action_taken_at": revision.created_at,
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
    if action == "processing":
        raise ApiError("processing events must not be stored in moderation_history")
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
    """
    Return the status timeline for an entity.

    Moderator/admin rows come from ``moderation_history``. For posts, synthetic
    ``processing`` rows are derived from revisions with
    ``triggered_moderation_review=True`` (never stored in moderation_history).
    """
    # Lazy import avoids feed.repositories ↔ feed.services circular import at startup.
    from apps.feed.repositories.post_revision_repository import get_processing_events

    rows = await get_history_by_entity_id(db, entity_id)
    timeline = [
        _format_history_item(history, moderator_user, moderator_profile)
        for history, moderator_user, moderator_profile in rows
    ]

    processing_revisions = await get_processing_events(db, entity_id)
    timeline.extend(_format_processing_event(rev) for rev in processing_revisions)

    timeline.sort(
        key=lambda item: (item["action_taken_at"], item["id"] is not None, str(item["id"] or "")),
        reverse=True,
    )
    return timeline
