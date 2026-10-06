from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.moderation.db_models import ModerationWordsConfig
from apps.moderation.db_models.moderation_words_db_model import utc_now
from apps.moderation.schemas import ModerationWordsData, UpdateModerationWordsRequest


def _normalize_words(words: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for word in words:
        cleaned = word.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _to_response_data(config: ModerationWordsConfig | None) -> dict:
    if config is None:
        return ModerationWordsData().model_dump()
    return ModerationWordsData(
        profanityWords=list(reversed(config.profanity_words or [])),
    ).model_dump()



async def _get_config(db: AsyncSession) -> ModerationWordsConfig | None:
    result = await db.execute(select(ModerationWordsConfig).limit(1))
    return result.scalar_one_or_none()


async def get_moderation_words(db: AsyncSession) -> dict:
    config = await _get_config(db)
    return _to_response_data(config)


async def update_moderation_words(
    payload: UpdateModerationWordsRequest,
    db: AsyncSession,
    *,
    actor_user_id=None,
    actor_role: str | None = None,
) -> dict:
    profanity_words = _normalize_words(payload.profanityWords)

    config = await _get_config(db)
    old_words = list(config.profanity_words or []) if config is not None else []
    if config is None:
        config = ModerationWordsConfig(
            profanity_words=profanity_words,
        )
        db.add(config)
    else:
        config.profanity_words = profanity_words
        config.updated_at = utc_now()
        db.add(config)

    await db.flush()
    await _log_profanity_word_changes(
        db,
        old_words=old_words,
        new_words=profanity_words,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        record_id=getattr(config, "id", None),
    )
    await db.commit()
    await db.refresh(config)
    return _to_response_data(config)


async def _log_profanity_word_changes(
    db: AsyncSession,
    *,
    old_words: list[str],
    new_words: list[str],
    actor_user_id,
    actor_role: str | None,
    record_id,
) -> None:
    if actor_user_id is None:
        return

    added = sorted(set(new_words) - set(old_words))
    removed = sorted(set(old_words) - set(new_words))
    if not added and not removed:
        return

    from apps.administration.repositories.admin_activity_log_repository import (
        PROFANITY_WORDS_ACTIVITY_MODULE,
    )
    from apps.administration.services.admin_activity_log_service import (
        create_admin_activity_log,
    )

    if added:
        await create_admin_activity_log(
            db,
            user_id=actor_user_id,
            role=actor_role,
            action="update",
            module=PROFANITY_WORDS_ACTIVITY_MODULE,
            record_id=record_id,
            description="updated profanity words",
            metadata={"added": added, "removed": [], "old": old_words, "new": new_words},
        )
    if removed:
        await create_admin_activity_log(
            db,
            user_id=actor_user_id,
            role=actor_role,
            action="delete",
            module=PROFANITY_WORDS_ACTIVITY_MODULE,
            record_id=record_id,
            description="deleted profanity words",
            metadata={"added": [], "removed": removed, "old": old_words, "new": new_words},
        )
