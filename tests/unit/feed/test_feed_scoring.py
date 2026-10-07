from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.feed.services.feed_scoring import (
    compute_relevance_score,
    has_relevance_match,
    is_feed_visible,
    viewer_has_relevance_criteria,
)
from apps.feed.services.feed_service import get_feed_service
from common.enums import ProfileVisibility, PostState


VIEWER_UNIVERSITY = uuid.uuid4()
AUTHOR_UNIVERSITY = uuid.uuid4()


def test_public_profile_major_match():
    score = compute_relevance_score(
        viewer_major="Computer Science",
        viewer_minor="Math",
        viewer_university_id=VIEWER_UNIVERSITY,
        author_major="Computer Science",
        author_minor="Physics",
        author_university_id=AUTHOR_UNIVERSITY,
    )
    assert score == 1
    assert is_feed_visible(
        profile_visibility=ProfileVisibility.public,
        is_connected=False,
        relevance_score=score,
    )


def test_public_profile_minor_match():
    score = compute_relevance_score(
        viewer_major="Biology",
        viewer_minor="Statistics",
        viewer_university_id=VIEWER_UNIVERSITY,
        author_major="Chemistry",
        author_minor="Statistics",
        author_university_id=AUTHOR_UNIVERSITY,
    )
    assert score == 1
    assert is_feed_visible(
        profile_visibility=ProfileVisibility.public,
        is_connected=False,
        relevance_score=score,
    )


def test_public_profile_university_match():
    score = compute_relevance_score(
        viewer_major="Biology",
        viewer_minor="Math",
        viewer_university_id=VIEWER_UNIVERSITY,
        author_major="Chemistry",
        author_minor="Physics",
        author_university_id=VIEWER_UNIVERSITY,
    )
    assert score == 1
    assert is_feed_visible(
        profile_visibility=ProfileVisibility.public,
        is_connected=False,
        relevance_score=score,
    )


def test_public_profile_no_matches_still_visible():
    score = compute_relevance_score(
        viewer_major="Biology",
        viewer_minor="Math",
        viewer_university_id=VIEWER_UNIVERSITY,
        author_major="Chemistry",
        author_minor="Physics",
        author_university_id=AUTHOR_UNIVERSITY,
    )
    assert score == 0
    assert not has_relevance_match(
        viewer_major="Biology",
        viewer_minor="Math",
        viewer_university_id=VIEWER_UNIVERSITY,
        author_major="Chemistry",
        author_minor="Physics",
        author_university_id=AUTHOR_UNIVERSITY,
    )
    assert is_feed_visible(
        profile_visibility=ProfileVisibility.public,
        is_connected=False,
        relevance_score=score,
        viewer_has_relevance_criteria=True,
    )


def test_public_profile_without_viewer_relevance_fields_includes_all():
    score = compute_relevance_score(
        viewer_major=None,
        viewer_minor=None,
        viewer_university_id=None,
        author_major="Chemistry",
        author_minor="Physics",
        author_university_id=AUTHOR_UNIVERSITY,
    )
    assert score == 0
    assert not viewer_has_relevance_criteria(
        viewer_major=None,
        viewer_minor=None,
        viewer_university_id=None,
    )
    assert is_feed_visible(
        profile_visibility=ProfileVisibility.public,
        is_connected=False,
        relevance_score=score,
        viewer_has_relevance_criteria=False,
    )


def test_feed_ordering_created_at_only_when_viewer_has_no_relevance_fields():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    yesterday = datetime(2026, 7, 12, tzinfo=timezone.utc)

    posts = [
        SimpleNamespace(id=1, relevance_score=3, created_at=yesterday),
        SimpleNamespace(id=2, relevance_score=1, created_at=now),
        SimpleNamespace(id=3, relevance_score=2, created_at=now),
    ]
    ordered = sorted(posts, key=lambda post: -post.created_at.timestamp())
    assert [post.id for post in ordered] == [2, 3, 1]


def test_private_profile_connected_included():
    score = compute_relevance_score(
        viewer_major="Biology",
        viewer_minor="Math",
        viewer_university_id=VIEWER_UNIVERSITY,
        author_major="Chemistry",
        author_minor="Physics",
        author_university_id=AUTHOR_UNIVERSITY,
    )
    assert is_feed_visible(
        profile_visibility=ProfileVisibility.private,
        is_connected=True,
        relevance_score=score,
    )


def test_private_profile_non_connected_excluded():
    score = compute_relevance_score(
        viewer_major="Biology",
        viewer_minor="Math",
        viewer_university_id=VIEWER_UNIVERSITY,
        author_major="Biology",
        author_minor="Math",
        author_university_id=VIEWER_UNIVERSITY,
    )
    assert not is_feed_visible(
        profile_visibility=ProfileVisibility.private,
        is_connected=False,
        relevance_score=score,
    )


def test_relevance_score_all_three_matches():
    score = compute_relevance_score(
        viewer_major="Computer Science",
        viewer_minor="Math",
        viewer_university_id=VIEWER_UNIVERSITY,
        author_major="computer science",
        author_minor=" math ",
        author_university_id=VIEWER_UNIVERSITY,
    )
    assert score == 3


def test_relevance_score_major_and_university_match():
    score = compute_relevance_score(
        viewer_major="Computer Science",
        viewer_minor="Math",
        viewer_university_id=VIEWER_UNIVERSITY,
        author_major="Computer Science",
        author_minor="Physics",
        author_university_id=VIEWER_UNIVERSITY,
    )
    assert score == 2


def test_feed_ordering_keeps_academic_and_connected_matches():
    """Academic matches and connected users stay in the feed; others drop."""
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    yesterday = datetime(2026, 7, 12, tzinfo=timezone.utc)
    two_days_ago = datetime(2026, 7, 11, tzinfo=timezone.utc)
    one_minute_ago = datetime(2026, 7, 13, 0, 1, 0, tzinfo=timezone.utc)

    posts = [
        SimpleNamespace(id="A", is_match=True, engagement=1, created_at=two_days_ago),  # university
        SimpleNamespace(id="B", is_match=True, engagement=10, created_at=yesterday),  # connected
        SimpleNamespace(id="C", is_match=True, engagement=5, created_at=now),  # major
        SimpleNamespace(id="D", is_match=False, engagement=99, created_at=one_minute_ago),  # neither
    ]
    max_match = max(int(post.is_match) for post in posts)
    visible = [post for post in posts if max_match == 0 or post.is_match]
    # Previous engagement-score order (kept for reference):
    # ordered = sorted(
    #     visible,
    #     key=lambda post: (-post.engagement, -post.created_at.timestamp()),
    # )
    # assert [post.id for post in ordered] == ["B", "C", "A"]
    ordered = sorted(visible, key=lambda post: -post.created_at.timestamp())
    assert [post.id for post in ordered] == ["C", "B", "A"]


def test_feed_ordering_falls_back_to_all_visible_when_no_matches():
    """When nothing matches university/major/minor, show all visible by time DESC."""
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    yesterday = datetime(2026, 7, 12, tzinfo=timezone.utc)
    two_days_ago = datetime(2026, 7, 11, tzinfo=timezone.utc)
    one_minute_ago = datetime(2026, 7, 13, 0, 1, 0, tzinfo=timezone.utc)

    posts = [
        SimpleNamespace(id="A", is_match=False, created_at=two_days_ago),
        SimpleNamespace(id="B", is_match=False, created_at=yesterday),
        SimpleNamespace(id="C", is_match=False, created_at=now),
        SimpleNamespace(id="D", is_match=False, created_at=one_minute_ago),
    ]
    max_match = max(int(post.is_match) for post in posts)
    visible = [post for post in posts if max_match == 0 or post.is_match]
    ordered = sorted(visible, key=lambda post: -post.created_at.timestamp())
    assert [post.id for post in ordered] == ["D", "C", "B", "A"]


@pytest.mark.asyncio
async def test_feed_service_pagination(mock_db):
    user_id = uuid.uuid4()
    post_one = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=PostState.published,
        revision_number=1,
        content={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )
    post_two = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=post_one.author_user_id,
        state=PostState.published,
        revision_number=1,
        content={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )
    filler_one = SimpleNamespace(**{**post_one.__dict__, "id": uuid.uuid4()})
    filler_two = SimpleNamespace(**{**post_two.__dict__, "id": uuid.uuid4()})
    author_profile = SimpleNamespace(
        first_name="A",
        last_name="B",
        profile_photo_url=None,
        user_id=post_one.author_user_id,
    )

    db = mock_db()
    page_rows = [
        (filler_one, author_profile),
        (filler_two, author_profile),
        (post_one, author_profile),
        (post_two, author_profile),
    ]

    with (
        patch("apps.feed.services.feed_service.count_feed_posts", AsyncMock(return_value=5)) as count_posts,
        patch(
            "apps.feed.services.feed_service.fetch_feed_posts",
            AsyncMock(return_value=(page_rows, None)),
        ) as fetch_posts,
        patch(
            "apps.feed.services.feed_service._load_feed_enrichment_and_user_state",
            AsyncMock(
                return_value=SimpleNamespace(
                    enrichment=SimpleNamespace(
                        profile_details={},
                        requested_user_ids=set(),
                        connected_user_ids=set(),
                    ),
                    user_state=SimpleNamespace(
                        engagement=SimpleNamespace(
                            user_reaction_for=lambda pid: None,
                            reposted_post_ids=frozenset(),
                            bookmarked_post_ids=frozenset(),
                        ),
                        latest_reactions={},
                    ),
                )
            ),
        ),
    ):
        posts, total, _next_cursor = await get_feed_service(
            user_id,
            db,
            page=2,
            page_size=2,
            include_total=True,
        )

    count_posts.assert_awaited_once()
    fetch_posts.assert_awaited_once_with(
        db,
        user_id,
        None,
        set(),
        cursor=None,
        limit=4,
    )
    assert total == 5
    assert len(posts) == 2
    assert posts[0]["first_name"] == "A"
    assert posts[0]["author_user_id"] == post_one.author_user_id


@pytest.mark.asyncio
async def test_feed_service_without_pagination_fetches_all(mock_db):
    user_id = uuid.uuid4()
    db = mock_db()

    with (
        patch("apps.feed.services.feed_service.count_feed_posts", AsyncMock(return_value=0)),
        patch(
            "apps.feed.services.feed_service.fetch_feed_posts",
            AsyncMock(return_value=([], None)),
        ) as fetch_posts,
    ):
        posts, total, _next_cursor = await get_feed_service(user_id, db, include_total=True)

    fetch_posts.assert_awaited_once_with(
        db,
        user_id,
        None,
        set(),
        cursor=None,
        limit=None,
    )
    assert posts == []
    assert total == 0


def test_encode_decode_cursor_roundtrip():
    from apps.feed.services.feed_cursor import decode_cursor, encode_cursor

    post_id = uuid.uuid4()
    created_at = datetime(2026, 7, 18, 12, 30, 0, tzinfo=timezone.utc)
    # cursor = encode_cursor(engagement_score=9, created_at=created_at, post_id=post_id)
    cursor = encode_cursor(created_at=created_at, post_id=post_id)
    decoded = decode_cursor(cursor)
    # assert decoded["engagement_score"] == 9
    assert decoded["created_at"] == created_at
    assert decoded["id"] == post_id


def test_decode_cursor_accepts_legacy_relevance_payload():
    """Old clients may still send cursors that include relevance/engagement; ignore them."""
    import base64
    import json

    from apps.feed.services.feed_cursor import decode_cursor

    post_id = uuid.uuid4()
    created_at = datetime(2026, 7, 18, 12, 30, 0, tzinfo=timezone.utc)
    legacy = {
        "relevance": 6,
        "engagement_score": 9,
        "created_at": created_at.isoformat(),
        "id": str(post_id),
    }
    cursor = base64.urlsafe_b64encode(
        json.dumps(legacy, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    decoded = decode_cursor(cursor)
    # assert decoded["engagement_score"] == 9
    assert decoded["created_at"] == created_at
    assert decoded["id"] == post_id
    assert "relevance" not in decoded
    assert "engagement_score" not in decoded


def test_decode_cursor_rejects_malformed():
    from apps.feed.services.feed_cursor import decode_cursor
    from common.exceptions import ApiError
    import pytest as _pytest

    with _pytest.raises(ApiError, match="Invalid cursor"):
        decode_cursor("not-a-valid-cursor")


@pytest.mark.asyncio
async def test_get_feed_route_includes_next_cursor_and_has_more():
    """Paginated feed body keeps existing fields and appends cursor metadata."""
    from fastapi import Response

    from apps.feed.routes import get_feed

    user = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()
    response = Response()

    with (
        patch(
            "apps.feed.routes.get_feed_service",
            AsyncMock(return_value=([{"id": "post-1"}], 5, "opaque-cursor")),
        ),
        patch(
            "apps.analytics.services.add_user_activity_log_best_effort",
            AsyncMock(),
        ),
    ):
        result = await get_feed(
            response=response,
            page=1,
            pageSize=2,
            cursor=None,
            current_user=user,
            db=db,
        )

    data = result.data
    assert data["items"] == [{"id": "post-1"}]
    assert data["page"] == 1
    assert data["pageSize"] == 2
    assert data["totalItems"] == 5
    assert data["totalPages"] == 3
    assert data["next_cursor"] == "opaque-cursor"
    assert data["has_more"] is True
    assert response.headers["X-Next-Cursor"] == "opaque-cursor"


@pytest.mark.asyncio
async def test_get_feed_route_has_more_false_when_no_next_cursor():
    from fastapi import Response

    from apps.feed.routes import get_feed

    user = SimpleNamespace(id=uuid.uuid4())
    db = AsyncMock()
    response = Response()

    with (
        patch(
            "apps.feed.routes.get_feed_service",
            AsyncMock(return_value=([{"id": "post-1"}], 1, None)),
        ),
        patch(
            "apps.analytics.services.add_user_activity_log_best_effort",
            AsyncMock(),
        ),
    ):
        result = await get_feed(
            response=response,
            page=1,
            pageSize=20,
            cursor=None,
            current_user=user,
            db=db,
        )

    assert result.data["next_cursor"] is None
    assert result.data["has_more"] is False
    assert "X-Next-Cursor" not in response.headers
