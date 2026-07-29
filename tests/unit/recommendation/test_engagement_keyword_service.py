from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.recommendation.services.engagement_keyword_service import (
    ENGAGEMENT_WEIGHTS,
    apply_engagement_keyword_update,
    merge_post_keyword_names,
    remove_engagement_keywords,
    update_engagement_keywords,
)


def test_update_engagement_keywords_normalizes_and_dedupes() -> None:
    existing = {"python": 2}

    updated = update_engagement_keywords(
        existing,
        ["Machine Learning", "machine learning", "RAG"],
        increment=1,
    )

    assert updated == {
        "python": 2,
        "machine learning": 1,
        "rag": 1,
    }


def test_remove_engagement_keywords_deletes_zero_or_negative_scores() -> None:
    existing = {
        "machine learning": 6,
        "python": 2,
        "semantic search": 1,
    }

    updated = remove_engagement_keywords(
        existing,
        ["machine learning", "semantic search", "rag"],
        decrement=1,
    )

    assert updated == {
        "machine learning": 5,
        "python": 2,
    }


def test_merge_post_keyword_names_uses_keys_only() -> None:
    names = merge_post_keyword_names(
        {"machine learning": 2, "rag": 1},
        {"python": 1, "ai": 1},
    )

    assert names == ["machine learning", "rag", "python", "ai"]


def test_engagement_weights_constants() -> None:
    assert ENGAGEMENT_WEIGHTS == {
        "like": 1,
        "bookmark": 2,
        "comment": 3,
        "repost": 4,
    }


@pytest.mark.asyncio
async def test_apply_engagement_keyword_update_like_increments_profile(mock_db) -> None:
    user_id = uuid4()
    post_id = uuid4()

    post = SimpleNamespace(
        id=post_id,
        extracted_keywords={
            "content_keywords": {"machine learning": 2, "semantic search": 1},
            "hashtags": {"python": 1, "ai": 1},
        },
    )
    profile = SimpleNamespace(
        user_id=user_id,
        extracted_keywords={
            "engagement_keywords": {"machine learning": 5, "python": 2},
            "content_keywords": {},
            "hashtags": {},
        },
        keywords_updated_at=None,
    )

    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: post),
            SimpleNamespace(scalar_one_or_none=lambda: profile),
        ]
    )
    db.add = MagicMock()
    db.commit = AsyncMock()

    await apply_engagement_keyword_update(
        db,
        user_id,
        post_id,
        "like",
        added=True,
    )

    assert profile.extracted_keywords["engagement_keywords"] == {
        "machine learning": 6,
        "python": 3,
        "semantic search": 1,
        "ai": 1,
    }
    assert profile.keywords_updated_at is not None


@pytest.mark.asyncio
async def test_apply_engagement_keyword_update_unlike_decrements_profile(mock_db) -> None:
    user_id = uuid4()
    post_id = uuid4()

    post = SimpleNamespace(
        id=post_id,
        extracted_keywords={
            "content_keywords": {"machine learning": 2, "semantic search": 1},
            "hashtags": {},
        },
    )
    profile = SimpleNamespace(
        user_id=user_id,
        extracted_keywords={
            "engagement_keywords": {
                "machine learning": 1,
                "semantic search": 1,
            },
        },
        keywords_updated_at=None,
    )

    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: post),
            SimpleNamespace(scalar_one_or_none=lambda: profile),
        ]
    )
    db.add = MagicMock()
    db.commit = AsyncMock()

    await apply_engagement_keyword_update(
        db,
        user_id,
        post_id,
        "like",
        added=False,
    )

    assert profile.extracted_keywords["engagement_keywords"] == {}
