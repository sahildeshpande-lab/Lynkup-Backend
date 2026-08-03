from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.recommendation.services.post_backfill_service import (
    PostBackfillService,
    _keyword_count,
)
from common.enums import PostState


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def test_keyword_count_sums_score_fields() -> None:
    assert _keyword_count({"hashtags": {"ai": 1}, "content_keywords": {"rag": 1, "nlp": 2}}) == 3


@pytest.mark.asyncio
async def test_backfill_one_post_skips_when_keywords_exist() -> None:
    post_id = uuid4()
    user_id = uuid4()
    post = SimpleNamespace(
        id=post_id,
        author_user_id=user_id,
        state=PostState.published,
        extracted_keywords={"hashtags": {}, "content_keywords": {"x": 1}},
        keywords_updated_at=None,
        content={"caption": "hello"},
    )
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: post)
    )
    session.commit = AsyncMock()

    result = await PostBackfillService()._backfill_one_post(session, post_id=post_id)

    assert result["status"] == "skipped"
    assert result["user_id"] == user_id
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_one_post_updates_null_keywords() -> None:
    post_id = uuid4()
    user_id = uuid4()
    post = SimpleNamespace(
        id=post_id,
        author_user_id=user_id,
        state=PostState.published,
        extracted_keywords=None,
        keywords_updated_at=None,
        content={"caption": "Machine Learning"},
    )
    snapshot = {"hashtags": {"ml": 1}, "content_keywords": {"machine learning": 1}}

    session = MagicMock()
    session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: post)
    )
    session.add = MagicMock()
    session.commit = AsyncMock()

    service = PostBackfillService()
    with patch.object(
        service,
        "_build_post_snapshot",
        AsyncMock(return_value=snapshot),
    ):
        result = await service._backfill_one_post(session, post_id=post_id)

    assert result["status"] == "updated"
    assert result["user_id"] == user_id
    assert result["keywords_generated"] == 2
    assert post.extracted_keywords == snapshot
    assert isinstance(post.keywords_updated_at, datetime)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_post_snapshot_reuses_existing_pipeline() -> None:
    user_id = uuid4()
    session = MagicMock()
    service = PostBackfillService()

    with patch(
        "apps.recommendation.services.post_backfill_service.build_post_recommendation_payload",
        AsyncMock(
            return_value={
                "major": ["CS"],
                "hashtags": {"profile": 1},
                "content_keywords": {"profile": 1},
                "_post_snapshot": {
                    "hashtags": {"ai": 1},
                    "content_keywords": {"rag": 1},
                },
            }
        ),
    ) as payload_mock:
        snapshot = await service._build_post_snapshot(
            session,
            user_id=user_id,
            content={"caption": "hello"},
        )

    payload_mock.assert_awaited_once()
    assert snapshot == {"hashtags": {"ai": 1}, "content_keywords": {"rag": 1}}


@pytest.mark.asyncio
async def test_empty_content_stores_empty_maps() -> None:
    user_id = uuid4()
    session = MagicMock()
    service = PostBackfillService()

    with patch(
        "apps.recommendation.services.post_backfill_service.build_post_recommendation_payload",
        AsyncMock(
            return_value={
                "_post_snapshot": {
                    "hashtags": {},
                    "content_keywords": {},
                }
            }
        ),
    ):
        snapshot = await service._build_post_snapshot(
            session,
            user_id=user_id,
            content=None,
        )

    assert snapshot == {"hashtags": {}, "content_keywords": {}}


@pytest.mark.asyncio
async def test_one_post_failure_does_not_stop_backfill(caplog) -> None:
    ok_post = uuid4()
    fail_post = uuid4()
    service = PostBackfillService()

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    async def _one(_session, *, post_id):
        if post_id == fail_post:
            raise RuntimeError("boom")
        return {
            "status": "updated",
            "user_id": uuid4(),
            "keywords_generated": 1,
        }

    caplog.set_level(logging.INFO)
    with (
        patch(
            "apps.recommendation.services.post_backfill_service.async_session_factory",
            return_value=session,
        ),
        patch.object(
            service,
            "_get_eligible_post_ids",
            AsyncMock(return_value=[fail_post, ok_post]),
        ),
        patch.object(service, "_backfill_one_post", side_effect=_one),
    ):
        await service.backfill_existing_posts()

    assert "failed=1" in caplog.text
    assert "updated=1" in caplog.text
    assert "Started" in caplog.text
