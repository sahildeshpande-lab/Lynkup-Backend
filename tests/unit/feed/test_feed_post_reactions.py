from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from apps.engagement.services.post_reaction_formatters import (
    build_post_reactions_from_rows,
    empty_post_reactions_group,
    group_post_reactor_profiles,
)
from apps.engagement.schemas import PostReactorProfile
from apps.feed.services.post_service import format_post_detail
from common.enums import ReactionType


def _reaction(reaction_type: ReactionType = ReactionType.like):
    return SimpleNamespace(
        reaction_type=reaction_type,
        created_at=datetime(2026, 7, 14, 9, 5, 15, tzinfo=timezone.utc),
    )


def _profile():
    return SimpleNamespace(
        id=uuid.uuid4(),
        first_name="test012",
        last_name="",
        profile_photo_url=None,
        bio=None,
        major=None,
        minor=None,
        edu_level=None,
    )


def test_build_post_reactions_from_rows_groups_latest_by_type():
    profile = _profile()
    rows = [
        (_reaction(ReactionType.like), profile, None),
        (_reaction(ReactionType.celebrate), profile, None),
    ]

    grouped = build_post_reactions_from_rows(rows)

    assert len(grouped.LIKE) == 1
    assert len(grouped.CELEBRATE) == 1
    assert grouped.LIKE[0].first_name == "test012"
    assert grouped.LIKE[0].reaction_type == "LIKE"
    assert grouped.LIKE[0].reacted_at == datetime(2026, 7, 14, 9, 5, 15, tzinfo=timezone.utc)


def test_empty_post_reactions_group_has_all_types():
    grouped = empty_post_reactions_group()
    assert grouped.LIKE == []
    assert grouped.CELEBRATE == []
    assert grouped.INSIGHTFUL == []
    assert grouped.SUPPORT == []
    assert grouped.CURIOUS == []


def test_group_post_reactor_profiles_respects_three_item_limit_per_bucket():
    reactors = [
        PostReactorProfile(
            profile_id=uuid.uuid4(),
            first_name="a",
            last_name="",
            profilePhoto_url=None,
            bio=None,
            reaction_type="LIKE",
            reacted_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )
        for _ in range(3)
    ]
    grouped = group_post_reactor_profiles(reactors)
    assert len(grouped.LIKE) == 3


def test_format_post_detail_includes_reactions_when_provided():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=1,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )
    reactions = build_post_reactions_from_rows([(_reaction(ReactionType.like), _profile(), None)])

    data = format_post_detail(post, reactions=reactions)

    assert len(data["reactions"]["LIKE"]) == 1
    assert data["reactions"]["LIKE"][0]["reaction_type"] == "LIKE"


def test_format_post_detail_defaults_to_empty_reactions():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )

    data = format_post_detail(post)

    assert data["reactions"]["LIKE"] == []
    assert data["reactions"]["CELEBRATE"] == []
