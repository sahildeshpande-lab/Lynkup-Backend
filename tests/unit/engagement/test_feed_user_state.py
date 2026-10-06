"""Phase 3: combined feed user-state (engagement + latest reactions)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.engagement.repositories.engagement_repository import PostEngagementFlags
from apps.engagement.repositories.feed_user_state import (
    FeedUserState,
    _FEED_USER_STATE_SQL,
    fetch_feed_user_state,
    map_feed_user_state_payload,
)
from apps.engagement.schemas import PostReactionsGrouped
from apps.engagement.services.post_reaction_formatters import (
    build_post_reactions_from_rows,
)
from common.enums import ReactionType


def _empty_group() -> PostReactionsGrouped:
    return PostReactionsGrouped()


def _dt(hour: int = 1) -> datetime:
    return datetime(2026, 1, 1, hour, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_fetch_feed_user_state_empty_post_list():
    db = AsyncMock()
    state = await fetch_feed_user_state(db, uuid.uuid4(), [])
    assert state.engagement == PostEngagementFlags.empty()
    assert state.latest_reactions == {}
    db.execute.assert_not_called()


def test_no_engagement_and_no_reactions():
    post_id = uuid.uuid4()
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[],
        viewer_bookmarks=[],
        viewer_reposts=[],
        latest_reactions=[],
    )
    assert state.engagement.user_reaction_for(post_id) is None
    assert post_id not in state.engagement.bookmarked_post_ids
    assert post_id not in state.engagement.reposted_post_ids
    assert state.latest_reactions[post_id] == _empty_group()


def test_viewer_reaction_only():
    post_id = uuid.uuid4()
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[{"post_id": str(post_id), "reaction_type": "like"}],
        viewer_bookmarks=[],
        viewer_reposts=[],
        latest_reactions=[],
    )
    assert state.engagement.user_reaction_for(post_id) == ReactionType.like
    assert post_id not in state.engagement.bookmarked_post_ids


def test_bookmarked_only():
    post_id = uuid.uuid4()
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[],
        viewer_bookmarks=[str(post_id)],
        viewer_reposts=[],
        latest_reactions=[],
    )
    assert post_id in state.engagement.bookmarked_post_ids


def test_reposted_only():
    post_id = uuid.uuid4()
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[],
        viewer_bookmarks=[],
        viewer_reposts=[str(post_id)],
        latest_reactions=[],
    )
    assert post_id in state.engagement.reposted_post_ids


def test_all_three_viewer_states():
    post_id = uuid.uuid4()
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[{"post_id": post_id, "reaction_type": "celebrate"}],
        viewer_bookmarks=[post_id],
        viewer_reposts=[post_id],
        latest_reactions=[],
    )
    assert state.engagement.user_reaction_for(post_id) == ReactionType.celebrate
    assert post_id in state.engagement.bookmarked_post_ids
    assert post_id in state.engagement.reposted_post_ids


def test_one_reaction_type_latest():
    post_id = uuid.uuid4()
    reactor = uuid.uuid4()
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[],
        viewer_bookmarks=[],
        viewer_reposts=[],
        latest_reactions=[
            {
                "post_id": post_id,
                "reaction_type": "like",
                "created_at": _dt(3).isoformat(),
                "profile_user_id": reactor,
                "first_name": "Ann",
                "last_name": "A",
                "profile_photo_url": None,
                "bio": None,
                "university_name": "U1",
            }
        ],
    )
    grouped = state.latest_reactions[post_id]
    assert len(grouped.LIKE) == 1
    assert grouped.LIKE[0].first_name == "Ann"
    assert grouped.LIKE[0].reaction_type == "LIKE"
    assert grouped.CELEBRATE == []


def test_multiple_reaction_types():
    post_id = uuid.uuid4()
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[],
        viewer_bookmarks=[],
        viewer_reposts=[],
        latest_reactions=[
            {
                "post_id": post_id,
                "reaction_type": "like",
                "created_at": _dt(2).isoformat(),
                "profile_user_id": uuid.uuid4(),
                "first_name": "L",
                "last_name": None,
                "profile_photo_url": None,
                "bio": "b",
                "university_name": None,
            },
            {
                "post_id": post_id,
                "reaction_type": "support",
                "created_at": _dt(1).isoformat(),
                "profile_user_id": uuid.uuid4(),
                "first_name": "S",
                "last_name": None,
                "profile_photo_url": None,
                "bio": None,
                "university_name": None,
            },
        ],
    )
    assert len(state.latest_reactions[post_id].LIKE) == 1
    assert len(state.latest_reactions[post_id].SUPPORT) == 1


def test_more_than_three_reactors_keeps_payload_order_max_three_in_sql_contract():
    """Mapper keeps rows as returned; SQL applies rn <= 3. Simulate 3 kept rows."""
    post_id = uuid.uuid4()
    rows = []
    for hour in (5, 4, 3):
        rows.append(
            {
                "post_id": post_id,
                "reaction_type": "like",
                "created_at": _dt(hour).isoformat(),
                "profile_user_id": uuid.uuid4(),
                "first_name": f"H{hour}",
                "last_name": None,
                "profile_photo_url": None,
                "bio": None,
                "university_name": None,
            }
        )
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[],
        viewer_bookmarks=[],
        viewer_reposts=[],
        latest_reactions=rows,
    )
    names = [r.first_name for r in state.latest_reactions[post_id].LIKE]
    assert names == ["H5", "H4", "H3"]


def test_latest_ordering_unchanged_within_type():
    post_id = uuid.uuid4()
    # Already ordered created_at DESC as SQL json_agg would emit.
    newer = _dt(9)
    older = _dt(1)
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[],
        viewer_bookmarks=[],
        viewer_reposts=[],
        latest_reactions=[
            {
                "post_id": post_id,
                "reaction_type": "curious",
                "created_at": newer.isoformat(),
                "profile_user_id": uuid.uuid4(),
                "first_name": "New",
                "last_name": None,
                "profile_photo_url": None,
                "bio": None,
                "university_name": None,
            },
            {
                "post_id": post_id,
                "reaction_type": "curious",
                "created_at": older.isoformat(),
                "profile_user_id": uuid.uuid4(),
                "first_name": "Old",
                "last_name": None,
                "profile_photo_url": None,
                "bio": None,
                "university_name": None,
            },
        ],
    )
    assert [r.first_name for r in state.latest_reactions[post_id].CURIOUS] == ["New", "Old"]
    assert state.latest_reactions[post_id].CURIOUS[0].reacted_at == newer


def test_null_profile_university_behavior():
    post_id = uuid.uuid4()
    state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[],
        viewer_bookmarks=[],
        viewer_reposts=[],
        latest_reactions=[
            {
                "post_id": post_id,
                "reaction_type": "insightful",
                "created_at": _dt().isoformat(),
                "profile_user_id": None,
                "first_name": None,
                "last_name": None,
                "profile_photo_url": None,
                "bio": None,
                "university_name": None,
            }
        ],
    )
    reactor = state.latest_reactions[post_id].INSIGHTFUL[0]
    assert reactor.profile_id is None
    assert reactor.first_name is None
    assert reactor.profilePhoto_url is None


def test_multiple_posts_mixed_states():
    p1, p2, p3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    state = map_feed_user_state_payload(
        post_ids=[p1, p2, p3],
        viewer_reactions=[{"post_id": p1, "reaction_type": "like"}],
        viewer_bookmarks=[p2],
        viewer_reposts=[p3],
        latest_reactions=[
            {
                "post_id": p2,
                "reaction_type": "like",
                "created_at": _dt().isoformat(),
                "profile_user_id": uuid.uuid4(),
                "first_name": "X",
                "last_name": None,
                "profile_photo_url": None,
                "bio": None,
                "university_name": "Uni",
            }
        ],
    )
    assert state.engagement.user_reaction_for(p1) == ReactionType.like
    assert p2 in state.engagement.bookmarked_post_ids
    assert p3 in state.engagement.reposted_post_ids
    assert state.latest_reactions[p1] == _empty_group()
    assert len(state.latest_reactions[p2].LIKE) == 1
    assert state.latest_reactions[p3] == _empty_group()


def test_regression_equivalence_vs_legacy_row_builder():
    """New mapper output matches legacy build_post_reactions_from_rows for same data."""
    post_id = uuid.uuid4()
    reactor_id = uuid.uuid4()
    created = _dt(4)
    legacy_reaction = SimpleNamespace(
        post_id=post_id,
        reaction_type=ReactionType.like,
        created_at=created,
    )
    legacy_profile = SimpleNamespace(
        user_id=reactor_id,
        first_name="Ada",
        last_name="L",
        profile_photo_url=None,
        bio="bio",
        major=None,
        minor=None,
        edu_level=None,
    )
    legacy_university = SimpleNamespace(name="MIT")
    legacy_grouped = build_post_reactions_from_rows(
        [(legacy_reaction, legacy_profile, legacy_university)]
    )

    new_state = map_feed_user_state_payload(
        post_ids=[post_id],
        viewer_reactions=[{"post_id": post_id, "reaction_type": "like"}],
        viewer_bookmarks=[post_id],
        viewer_reposts=[],
        latest_reactions=[
            {
                "post_id": post_id,
                "reaction_type": "like",
                "created_at": created.isoformat(),
                "profile_user_id": reactor_id,
                "first_name": "Ada",
                "last_name": "L",
                "profile_photo_url": None,
                "bio": "bio",
                "university_name": "MIT",
            }
        ],
    )

    assert new_state.latest_reactions[post_id].model_dump() == legacy_grouped.model_dump()
    assert new_state.engagement.user_reaction_for(post_id) == ReactionType.like
    assert post_id in new_state.engagement.bookmarked_post_ids


@pytest.mark.asyncio
async def test_sql_uses_expanding_bind_and_single_execute():
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mapping = MagicMock()
    mapping.mappings.return_value.one.return_value = {
        "viewer_reactions": [],
        "viewer_bookmarks": [],
        "viewer_reposts": [],
        "latest_reactions": [],
    }
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mapping)

    state = await fetch_feed_user_state(db, user_id, [post_id], per_type_limit=3)
    assert isinstance(state, FeedUserState)
    assert db.execute.await_count == 1
    _stmt, params = db.execute.await_args.args[0], db.execute.await_args.args[1]
    assert params["user_id"] == user_id
    assert params["post_ids"] == [post_id]
    assert params["per_type_limit"] == 3
    assert ":post_ids" in _FEED_USER_STATE_SQL
    assert "row_number()" in _FEED_USER_STATE_SQL
    assert "json_agg" in _FEED_USER_STATE_SQL
    # No Cartesian join of engagement to reactions.
    assert "viewer_reactions" in _FEED_USER_STATE_SQL
    assert "latest_reactions" in _FEED_USER_STATE_SQL


def test_sql_contract_limits_per_type():
    assert "rn <= :per_type_limit" in _FEED_USER_STATE_SQL
    assert "PARTITION BY pr.post_id, pr.reaction_type" in _FEED_USER_STATE_SQL
    assert "ORDER BY pr.created_at DESC" in _FEED_USER_STATE_SQL
