from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.recommendation.services.profile_backfill_service import (
    ProfileBackfillService,
    _keyword_count,
)
from common.enums import PostState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def test_keyword_count_sums_list_and_score_fields() -> None:
    record = {
        "major": ["CS"],
        "minor": [],
        "interests": ["AI", "ML"],
        "hashtags": {"ai": 1},
        "engagement_keywords": {},
        "content_keywords": {"rag": 2, "nlp": 1},
    }
    assert _keyword_count(record) == 6


def test_combine_post_text_joins_plain_content() -> None:
    service = ProfileBackfillService()
    posts = [
        SimpleNamespace(content={"caption": "I love Machine Learning"}),
        SimpleNamespace(content={"caption": "Semantic Search is amazing"}),
        SimpleNamespace(content={"caption": ""}),
    ]

    with patch(
        "apps.recommendation.services.profile_backfill_service.build_post_plain_text",
        side_effect=lambda content: (content or {}).get("caption") or "",
    ):
        combined = service._combine_post_text(posts)  # type: ignore[arg-type]

    assert combined == "I love Machine Learning\n\nSemantic Search is amazing"


@pytest.mark.asyncio
async def test_backfill_one_user_skips_when_keywords_exist() -> None:
    user_id = uuid4()
    profile = SimpleNamespace(
        user_id=user_id,
        extracted_keywords={"major": ["CS"]},
        keywords_updated_at=None,
        learning_recommendations={"keep": True},
        recommendations_updated_at=_utc_now(),
    )
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: profile)
    )
    session.add = MagicMock()
    session.commit = AsyncMock()

    service = ProfileBackfillService()
    result = await service._backfill_one_user(session, user_id=user_id)

    assert result["status"] == "skipped"
    session.commit.assert_not_awaited()
    assert profile.learning_recommendations == {"keep": True}


@pytest.mark.asyncio
async def test_backfill_one_user_updates_null_keywords_without_touching_recommendations() -> None:
    user_id = uuid4()
    profile = SimpleNamespace(
        user_id=user_id,
        extracted_keywords=None,
        keywords_updated_at=None,
        learning_recommendations=None,
        recommendations_updated_at=None,
    )
    posts = [
        SimpleNamespace(
            author_user_id=user_id,
            state=PostState.published,
            content={"caption": "Machine Learning rocks"},
            created_at=_utc_now(),
        )
    ]
    generated = {
        "major": ["Computer Science"],
        "minor": [],
        "interests": ["AI"],
        "hashtags": {"ml": 1},
        "engagement_keywords": {},
        "content_keywords": {"machine learning": 1},
    }

    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: profile),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: posts)),
        ]
    )
    session.add = MagicMock()
    session.commit = AsyncMock()

    service = ProfileBackfillService()
    with patch.object(
        service,
        "_build_extracted_keywords",
        AsyncMock(return_value=generated),
    ) as build_mock:
        result = await service._backfill_one_user(session, user_id=user_id)

    assert result["status"] == "updated"
    assert result["posts_processed"] == 1
    assert result["keywords_generated"] == 4
    assert profile.extracted_keywords == generated
    assert isinstance(profile.keywords_updated_at, datetime)
    assert profile.learning_recommendations is None
    assert profile.recommendations_updated_at is None
    build_mock.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_backfill_builds_via_existing_post_pipeline() -> None:
    user_id = uuid4()
    session = MagicMock()
    service = ProfileBackfillService()

    with patch(
        "apps.recommendation.services.profile_backfill_service.build_post_recommendation_payload",
        AsyncMock(
            return_value={
                "major": ["CS"],
                "minor": ["AI"],
                "interests": ["ML"],
                "hashtags": {"ai": 1},
                "engagement_keywords": {"should": "clear"},
                "content_keywords": {"rag": 1},
                "_post_snapshot": {"content_keywords": {"rag": 1}},
            }
        ),
    ) as payload_mock:
        record = await service._build_extracted_keywords(
            session,
            user_id=user_id,
            combined_text="hello world",
        )

    payload_mock.assert_awaited_once()
    assert payload_mock.await_args.kwargs["content"] == {"caption": "hello world"}
    assert record == {
        "major": ["CS"],
        "minor": ["AI"],
        "interests": ["ML"],
        "hashtags": {"ai": 1},
        "engagement_keywords": {},
        "content_keywords": {"rag": 1},
    }


@pytest.mark.asyncio
async def test_one_user_failure_does_not_stop_backfill(caplog) -> None:
    ok_user = uuid4()
    fail_user = uuid4()
    service = ProfileBackfillService()

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    async def _one(_session, *, user_id):
        if user_id == fail_user:
            raise RuntimeError("boom")
        return {
            "status": "updated",
            "posts_processed": 0,
            "keywords_generated": 2,
        }

    caplog.set_level(logging.INFO)
    with (
        patch(
            "apps.recommendation.services.profile_backfill_service.async_session_factory",
            return_value=session,
        ),
        patch.object(
            service,
            "_get_null_keyword_profile_ids",
            AsyncMock(return_value=[fail_user, ok_user]),
        ),
        patch.object(service, "_backfill_one_user", side_effect=_one),
    ):
        await service.backfill_existing_profiles()

    assert "failed=1" in caplog.text
    assert "updated=1" in caplog.text
    assert "Started" in caplog.text
