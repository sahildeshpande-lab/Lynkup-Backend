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
from common.enums import ProfileVisibility


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


def test_feed_ordering_matches_first_then_all_posts_newest_first():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    yesterday = datetime(2026, 7, 12, tzinfo=timezone.utc)
    two_days_ago = datetime(2026, 7, 11, tzinfo=timezone.utc)

    posts = [
        SimpleNamespace(id=1, is_match=False, created_at=now),
        SimpleNamespace(id=2, is_match=True, created_at=yesterday),
        SimpleNamespace(id=3, is_match=True, created_at=now),
        SimpleNamespace(id=4, is_match=False, created_at=yesterday),
        SimpleNamespace(id=5, is_match=False, created_at=two_days_ago),
    ]
    ordered = sorted(
        posts,
        key=lambda post: (-int(post.is_match), -post.created_at.timestamp()),
    )
    assert [post.id for post in ordered] == [3, 2, 1, 4, 5]


@pytest.mark.asyncio
async def test_feed_service_pagination(mock_db):
    user_id = uuid.uuid4()
    viewer_profile = SimpleNamespace(
        major="CS",
        minor="Math",
        university_id=VIEWER_UNIVERSITY,
    )
    post_one = SimpleNamespace(id=uuid.uuid4(), author_user_id=uuid.uuid4())
    post_two = SimpleNamespace(id=uuid.uuid4(), author_user_id=uuid.uuid4())
    author_profile = SimpleNamespace(first_name="A", last_name="B", profile_photo_url=None)

    db = mock_db()

    with (
        patch("apps.feed.services.feed_service.fetch_viewer_profile", AsyncMock(return_value=viewer_profile)),
        patch("apps.feed.services.feed_service.get_user_connections", AsyncMock(return_value=set())),
        patch("apps.feed.services.feed_service.count_feed_posts", AsyncMock(return_value=5)) as count_posts,
        patch(
            "apps.feed.services.feed_service.fetch_feed_posts",
            AsyncMock(return_value=[(post_one, author_profile), (post_two, author_profile)]),
        ) as fetch_posts,
    ):
        posts, total = await get_feed_service(
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
        viewer_profile,
        set(),
        offset=2,
        limit=2,
    )
    assert total == 5
    assert len(posts) == 2
    assert posts[0]._author_profile is author_profile


@pytest.mark.asyncio
async def test_feed_service_without_pagination_fetches_all(mock_db):
    user_id = uuid.uuid4()
    db = mock_db()

    with (
        patch("apps.feed.services.feed_service.fetch_viewer_profile", AsyncMock(return_value=None)),
        patch("apps.feed.services.feed_service.get_user_connections", AsyncMock(return_value=set())),
        patch("apps.feed.services.feed_service.count_feed_posts", AsyncMock(return_value=0)),
        patch("apps.feed.services.feed_service.fetch_feed_posts", AsyncMock(return_value=[])) as fetch_posts,
    ):
        posts, total = await get_feed_service(user_id, db, include_total=True)

    fetch_posts.assert_awaited_once_with(
        db,
        user_id,
        None,
        set(),
        offset=0,
        limit=None,
    )
    assert posts == []
    assert total == 0
