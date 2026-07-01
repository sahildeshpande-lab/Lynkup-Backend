from __future__ import annotations

import pytest
import pytest_asyncio
from sqlmodel import select

from core.database.session import async_session_factory, engine
from core.database.init import init_db
from apps.moderation.db_models import ModerationWordsConfig
from apps.moderation.schemas import UpdateModerationWordsRequest
from apps.moderation.services.moderation_words_service import (
    get_moderation_words,
    update_moderation_words,
    _normalize_words,
)


@pytest_asyncio.fixture(autouse=True)
async def cleanup_moderation_config():
    async with async_session_factory() as session:
        result = await session.execute(select(ModerationWordsConfig))
        for row in result.scalars().all():
            await session.delete(row)
        await session.commit()
    yield
    async with async_session_factory() as session:
        result = await session.execute(select(ModerationWordsConfig))
        for row in result.scalars().all():
            await session.delete(row)
        await session.commit()


def test_normalize_words_dedupes_trims_and_lowercases() -> None:
    assert _normalize_words([" Free Money ", "free money", "", "  CLICK HERE  "]) == [
        "free money",
        "click here",
    ]


@pytest.mark.asyncio
async def test_get_moderation_words_empty() -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            data = await get_moderation_words(session)
            assert data == {"profanityWords": []}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_moderation_words_replaces_entire_list() -> None:
    try:
        await init_db()
        async with async_session_factory() as session:
            first = await update_moderation_words(
                UpdateModerationWordsRequest(
                    profanityWords=["BadWord", "Another"],
                ),
                session,
            )
            assert first == {
                "profanityWords": ["badword", "another"],
            }

            second = await update_moderation_words(
                UpdateModerationWordsRequest(
                    profanityWords=[],
                ),
                session,
            )
            assert second == {"profanityWords": []}

            fetched = await get_moderation_words(session)
            assert fetched == second

            configs = (await session.execute(select(ModerationWordsConfig))).scalars().all()
            assert len(configs) == 1
    finally:
        await engine.dispose()
