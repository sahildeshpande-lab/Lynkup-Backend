from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from apps.profiles.services import response_service as rs


@pytest.mark.asyncio
async def test_resolve_posts_count_visitor_uses_cached_public_count():
    db = AsyncMock()
    profile_user_id = uuid.uuid4()
    viewer_id = uuid.uuid4()

    count = await rs._resolve_posts_count(
        db,
        profile_user_id=profile_user_id,
        cached_posts_count=4,
        viewer_user_id=viewer_id,
    )

    assert count == 4


@pytest.mark.asyncio
async def test_resolve_posts_count_owner_includes_flagged():
    db = AsyncMock()
    profile_user_id = uuid.uuid4()

    with patch.object(
        rs,
        "_count_owner_visible_posts",
        AsyncMock(return_value=6),
    ) as count_fn:
        count = await rs._resolve_posts_count(
            db,
            profile_user_id=profile_user_id,
            cached_posts_count=4,
            viewer_user_id=profile_user_id,
        )

    assert count == 6
    count_fn.assert_awaited_once_with(db, profile_user_id)


@pytest.mark.asyncio
async def test_owner_visible_states_include_processing():
    from common.enums import OWNER_VISIBLE_POST_STATES, PostState, FEED_VISIBLE_POST_STATES

    assert PostState.processing in OWNER_VISIBLE_POST_STATES
    assert PostState.flagged in OWNER_VISIBLE_POST_STATES
    assert PostState.processing not in FEED_VISIBLE_POST_STATES
    assert PostState.flagged not in FEED_VISIBLE_POST_STATES
