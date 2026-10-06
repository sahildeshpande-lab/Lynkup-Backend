"""Phase 7 STEP4: combined enrichment + user-state."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.feed.services.feed_enrichment_user_state import (
    _COMBINED_ENRICHMENT_USER_STATE_SQL,
    load_feed_enrichment_and_user_state,
)


def test_combined_sql_uses_independent_json_aggregations():
    assert "json_agg" in _COMBINED_ENRICHMENT_USER_STATE_SQL
    assert "university_rows" in _COMBINED_ENRICHMENT_USER_STATE_SQL
    assert "connection_rows" in _COMBINED_ENRICHMENT_USER_STATE_SQL
    assert "moderation_rows" in _COMBINED_ENRICHMENT_USER_STATE_SQL
    assert "viewer_reactions" in _COMBINED_ENRICHMENT_USER_STATE_SQL
    assert "latest_reactions" in _COMBINED_ENRICHMENT_USER_STATE_SQL
    # No cross-domain joins that would multiply rows.
    assert "JOIN interest_rows" not in _COMBINED_ENRICHMENT_USER_STATE_SQL
    assert "JOIN viewer_reactions" not in _COMBINED_ENRICHMENT_USER_STATE_SQL
    assert "JOIN connection_rows" not in _COMBINED_ENRICHMENT_USER_STATE_SQL


@pytest.mark.asyncio
async def test_load_combined_empty_skips_sql():
    db = AsyncMock()
    result = await load_feed_enrichment_and_user_state(
        db, uuid.uuid4(), {}, [], per_type_limit=3
    )
    db.execute.assert_not_called()
    assert result.enrichment.profile_details == {}
    assert result.enrichment.connected_user_ids == set()
    assert result.user_state.latest_reactions == {}


@pytest.mark.asyncio
async def test_load_combined_single_sql_round_trip():
    user_id = uuid.uuid4()
    uni_id = uuid.uuid4()
    viewer = uuid.uuid4()
    post_id = uuid.uuid4()
    profile = SimpleNamespace(
        university_id=uni_id,
        profile_interests_id=[1],
        bio="b",
        major="m",
        minor=None,
        edu_level=None,
    )
    mapping = MagicMock()
    mapping.mappings.return_value.one.return_value = {
        "universities": [{"id": str(uni_id), "name": "U", "website": None}],
        "interests": [{"id": 1, "name": "AI"}],
        "requested_user_ids": [str(user_id)],
        "connected_user_ids": [str(user_id)],
        "triggered_moderation_post_ids": [str(post_id)],
        "viewer_reactions": [{"post_id": str(post_id), "reaction_type": "like"}],
        "viewer_bookmarks": [str(post_id)],
        "viewer_reposts": [],
        "latest_reactions": [],
    }
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mapping)

    result = await load_feed_enrichment_and_user_state(
        db,
        viewer,
        {user_id: profile},
        [post_id],
        per_type_limit=3,
    )
    assert db.execute.await_count == 1
    assert result.enrichment.profile_details[user_id]["university"] == "U"
    assert result.enrichment.connected_user_ids == {user_id}
    assert result.enrichment.requested_user_ids == {user_id}
    assert post_id in result.user_state.engagement.bookmarked_post_ids
    assert result.user_state.engagement.user_reaction_for(post_id) is not None
    assert result.triggered_moderation_post_ids == {post_id}
