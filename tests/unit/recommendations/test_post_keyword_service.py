from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.recommendations.services.post_keyword_service import (
    build_post_plain_text,
    build_post_recommendation_payload,
    log_post_keywords_best_effort,
)


def test_build_post_plain_text_combines_caption_and_html() -> None:
    text = build_post_plain_text(
        {"caption": "Hello #AI", "content_html": "<p>Learning <strong>FastAPI</strong></p>"}
    )
    assert "Hello #AI" in text
    assert "Learning FastAPI" in text


@pytest.mark.asyncio
async def test_build_post_recommendation_payload_merges_keyword_scores(mock_db) -> None:
    user_id = uuid4()
    profile = SimpleNamespace(
        major="Artificial Intelligence",
        minor="Data Science",
        profile_interests_id=[1],
        extracted_keywords={
            "content_keywords": {"existing keyword": 2},
            "hashtags": {"ai": 3},
        },
    )
    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: profile),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["Machine Learning"])),
        ]
    )

    with patch(
        "apps.recommendations.services.post_keyword_service._extract_keywords_sync",
        return_value={"hashtags": ["ignored"], "keywords": ["existing keyword", "vector database"]},
    ):
        payload = await build_post_recommendation_payload(
            db,
            user_id=user_id,
            content={"caption": "Learning #FastAPI"},
        )

    assert payload["content_keywords"]["existing keyword"] == 3
    assert payload["content_keywords"]["vector database"] == 1
    assert payload["hashtags"]["fastapi"] == 1


@pytest.mark.asyncio
async def test_log_post_keywords_best_effort_does_not_raise(mock_db) -> None:
    with patch(
        "apps.recommendations.services.post_keyword_service.build_post_recommendation_payload",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await log_post_keywords_best_effort(
            uuid4(),
            {"caption": "x"},
            user_id=uuid4(),
            db=mock_db(),
        )
