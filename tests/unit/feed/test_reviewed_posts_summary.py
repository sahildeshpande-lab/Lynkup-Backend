from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch
import pytest

from apps.feed.services import post_service as ps


@pytest.mark.asyncio
async def test_list_reviewed_posts_by_state_service_includes_summary():
    db = AsyncMock()
    moderator_id = uuid.uuid4()

    mock_summary = {
        "published": 10,
        "flagged": 2,
        "rejected": 1,
        "reinstate": 0,
    }

    # We mock the repository methods at their definition source
    with (
        patch("apps.feed.services.post_service._repair_unassigned_moderators_for_state", AsyncMock()) as mock_repair,
        patch("apps.feed.repositories.post_repository.count_reviewed_posts_for_moderator", AsyncMock(return_value=12)) as mock_count,
        patch("apps.feed.repositories.post_repository.count_reviewed_posts_summary_by_state", AsyncMock(return_value=mock_summary)) as mock_sum_count,
        patch("apps.feed.repositories.post_repository.fetch_reviewed_posts_for_moderator", AsyncMock(return_value=[])) as mock_fetch,
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
