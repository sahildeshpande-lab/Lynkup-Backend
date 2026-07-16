from __future__ import annotations

import uuid
from types import SimpleNamespace

from apps.feed.services.post_service import (
    _format_reviewed_post_item,
    format_post_detail,
)


def test_format_post_detail_includes_engagement_fields():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=15,
        repost_count=4,
        share_count=10,
        comment_count=2,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )

    data = format_post_detail(
        post,
        is_liked=True,
        is_reposted=False,
        is_bookmarked=True,
        user_reaction="LIKE",
    )

    assert data["like_count"] == 15
    assert data["repost_count"] == 4
    assert data["share_count"] == 10
    assert data["comment_count"] == 2
    assert data["is_liked"] is True
    assert data["is_reposted"] is False
    assert data["is_bookmarked"] is True
    assert data["user_reaction"] == "LIKE"


def test_format_reviewed_post_item_includes_engagement_counts():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        content={"caption": "reviewed", "content_html": "<p>ok</p>"},
        state=SimpleNamespace(value="published"),
        is_moderator_reviewed=True,
        reviewed_at="2026-07-14T10:00:00Z",
        created_at="2026-07-14T09:00:00Z",
        like_count=12,
        repost_count=3,
        share_count=7,
        comment_count=5,
        moderator_id=uuid.uuid4(),
        attachments=[],
    )
    profile = SimpleNamespace(
        first_name="Ada",
        last_name="Lovelace",
        profile_photo_url=None,
    )

    data = _format_reviewed_post_item(post, profile)

    assert data["like_count"] == 12
    assert data["repost_count"] == 3
    assert data["share_count"] == 7
    assert data["comment_count"] == 5
    assert data["first_name"] == "Ada"
