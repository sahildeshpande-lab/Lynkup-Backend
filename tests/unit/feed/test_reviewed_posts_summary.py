from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch
import pytest

from apps.feed.services import post_service as ps
from common.enums import ReviewedPostOrder, ReviewedPostSort


@pytest.mark.asyncio
async def test_list_reviewed_posts_by_state_service_includes_summary():
    db = AsyncMock()
    moderator_id = uuid.uuid4()

    mock_summary = {
        "published": 10,
        "flagged": 2,
        "rejected": 1,
        "reinstate": 0,
        "escalate": 0,
    }

    # We mock the repository methods at their definition source
    with (
        patch("apps.feed.services.post_service._repair_unassigned_moderators_for_state", AsyncMock()) as mock_repair,
        patch("apps.feed.repositories.post_repository.count_reviewed_posts_for_moderator", AsyncMock(return_value=12)) as mock_count,
        patch("apps.feed.repositories.post_repository.count_reviewed_posts_summary_by_state", AsyncMock(return_value=mock_summary)) as mock_sum_count,
        patch("apps.feed.repositories.post_repository.fetch_reviewed_posts_for_moderator", AsyncMock(return_value=[])) as mock_fetch,
        patch(
            "apps.feed.repositories.post_revision_repository.posts_with_triggered_moderation_review",
            AsyncMock(return_value=set()),
        ),
    ):
        res = await ps.list_reviewed_posts_by_state_service(
            db,
            moderator_id=moderator_id,
            status="published",
            page=1,
            page_size=10,
        )

    assert "summary" in res
    assert res["summary"] == mock_summary
    mock_sum_count.assert_awaited_once_with(db, moderator_id)


@pytest.mark.asyncio
async def test_list_reviewed_posts_by_state_service_forwards_search():
    db = AsyncMock()
    moderator_id = uuid.uuid4()

    with (
        patch("apps.feed.services.post_service._repair_unassigned_moderators_for_state", AsyncMock()),
        patch(
            "apps.feed.repositories.post_repository.count_reviewed_posts_for_moderator",
            AsyncMock(return_value=0),
        ) as mock_count,
        patch(
            "apps.feed.repositories.post_repository.count_reviewed_posts_summary_by_state",
            AsyncMock(return_value={}),
        ),
        patch(
            "apps.feed.repositories.post_repository.fetch_reviewed_posts_for_moderator",
            AsyncMock(return_value=[]),
        ) as mock_fetch,
        patch(
            "apps.feed.repositories.post_revision_repository.posts_with_triggered_moderation_review",
            AsyncMock(return_value=set()),
        ),
    ):
        await ps.list_reviewed_posts_by_state_service(
            db,
            moderator_id=moderator_id,
            status="published",
            page=1,
            page_size=9,
            search="jane",
        )

    mock_count.assert_awaited_once_with(db, moderator_id, "published", search="jane")
    mock_fetch.assert_awaited_once_with(
        db,
        moderator_id,
        status="published",
        offset=0,
        limit=9,
        search="jane",
        sort=ReviewedPostSort.created_at,
        order=ReviewedPostOrder.desc,
    )


@pytest.mark.asyncio
async def test_list_reviewed_posts_by_state_service_forwards_sort():
    db = AsyncMock()
    moderator_id = uuid.uuid4()

    with (
        patch("apps.feed.services.post_service._repair_unassigned_moderators_for_state", AsyncMock()),
        patch(
            "apps.feed.repositories.post_repository.count_reviewed_posts_for_moderator",
            AsyncMock(return_value=0),
        ),
        patch(
            "apps.feed.repositories.post_repository.count_reviewed_posts_summary_by_state",
            AsyncMock(return_value={}),
        ),
        patch(
            "apps.feed.repositories.post_repository.fetch_reviewed_posts_for_moderator",
            AsyncMock(return_value=[]),
        ) as mock_fetch,
        patch(
            "apps.feed.repositories.post_revision_repository.posts_with_triggered_moderation_review",
            AsyncMock(return_value=set()),
        ),
    ):
        await ps.list_reviewed_posts_by_state_service(
            db,
            moderator_id=moderator_id,
            status="published",
            page=1,
            page_size=9,
            sort=ReviewedPostSort.updated_at,
            order=ReviewedPostOrder.asc,
        )

    mock_fetch.assert_awaited_once_with(
        db,
        moderator_id,
        status="published",
        offset=0,
        limit=9,
        search=None,
        sort=ReviewedPostSort.updated_at,
        order=ReviewedPostOrder.asc,
    )
