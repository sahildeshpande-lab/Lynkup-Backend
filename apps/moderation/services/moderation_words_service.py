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
        profanityWords=list(config.profanity_words or []),
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
) -> dict:
    profanity_words = _normalize_words(payload.profanityWords)

    config = await _get_config(db)
    if config is None:
        config = ModerationWordsConfig(
            profanity_words=profanity_words,
        )
        db.add(config)
    else:
        config.profanity_words = profanity_words
        config.updated_at = utc_now()
        db.add(config)

    await db.commit()
    await db.refresh(config)
    return _to_response_data(config)
