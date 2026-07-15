from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from apps.search.services import search_posts


def _user():
    return SimpleNamespace(id=uuid.uuid4())


def _post(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "author_user_id": uuid.uuid4(),
        "state": SimpleNamespace(value="published"),
        "revision_number": 1,
        "content": {"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        "created_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 13, tzinfo=timezone.utc),
        "like_count": 5,
        "repost_count": 1,
        "share_count": 2,
        "comment_count": 3,
        "is_moderator_reviewed": True,
        "reviewed_at": None,
        "moderator_id": None,
        "attachments": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _profile():
    return SimpleNamespace(
        first_name="Jane",
        last_name="Doe",
        profile_photo_url=None,
    )


@pytest.mark.asyncio
async def test_search_posts_keyword_search(mock_db):
    user = _user()
    post = _post()
    db = mock_db()
    rows = [(post, _profile(), None, None)]

    with (
        patch("apps.search.repositories.count_search_posts", AsyncMock(return_value=1)) as count_posts,
        patch("apps.search.repositories.search_posts_with_details", AsyncMock(return_value=rows)) as search_posts_repo,
        patch("apps.engagement.repositories.fetch_post_engagement_flags", AsyncMock()) as fetch_flags,
        patch("apps.feed.services.post_service.format_post_detail", return_value={"id": post.id, "like_count": 5}) as format_post,
    ):
        fetch_flags.return_value = SimpleNamespace(
            user_reaction_for=lambda _pid: None,
            reposted_post_ids=set(),
            bookmarked_post_ids=set(),
        )
        result = await search_posts(user, db, query="machine learning", page=1, page_size=20)

    count_posts.assert_awaited_once()
    assert count_posts.await_args.kwargs["query"] == "machine learning"
    search_posts_repo.assert_awaited_once()
    assert search_posts_repo.await_args.kwargs["query"] == "machine learning"
    assert result["totalItems"] == 1
    assert result["items"][0]["reaction_count"] == 5
    format_post.assert_called_once()


@pytest.mark.asyncio
async def test_search_posts_hashtag_filter(mock_db):
    user = _user()
    db = mock_db()

    with (
        patch("apps.search.repositories.count_search_posts", AsyncMock(return_value=0)) as count_posts,
        patch("apps.search.repositories.search_posts_with_details", AsyncMock(return_value=[])) as search_posts_repo,
    ):
        await search_posts(user, db, hashtag="#AI", page=1, page_size=20)

    assert count_posts.await_args.kwargs["hashtag"] == "#AI"
    assert search_posts_repo.await_args.kwargs["hashtag"] == "#AI"


@pytest.mark.asyncio
async def test_search_posts_university_filter(mock_db):
    user = _user()
    db = mock_db()

    with (
        patch("apps.search.repositories.count_search_posts", AsyncMock(return_value=0)),
        patch("apps.search.repositories.search_posts_with_details", AsyncMock(return_value=[])) as search_posts_repo,
    ):
        await search_posts(user, db, university_name="State University")

    assert search_posts_repo.await_args.kwargs["university_name"] == "State University"
    assert search_posts_repo.await_args.kwargs["limit"] is None


@pytest.mark.asyncio
async def test_search_posts_country_filter(mock_db):
    user = _user()
    db = mock_db()

    with (
        patch("apps.search.repositories.count_search_posts", AsyncMock(return_value=0)),
        patch("apps.search.repositories.search_posts_with_details", AsyncMock(return_value=[])) as search_posts_repo,
    ):
        await search_posts(user, db, country="India")

    assert search_posts_repo.await_args.kwargs["country"] == "India"


@pytest.mark.asyncio
async def test_search_posts_academic_interest_filter(mock_db):
    user = _user()
    db = mock_db()

    with (
        patch("apps.search.repositories.count_search_posts", AsyncMock(return_value=0)) as count_posts,
        patch("apps.search.repositories.search_posts_with_details", AsyncMock(return_value=[])) as search_posts_repo,
    ):
        await search_posts(user, db, academic_interest="Machine Learning")

    count_posts.assert_not_called()
    assert search_posts_repo.await_args.kwargs["academic_interest"] == "Machine Learning"


@pytest.mark.asyncio
async def test_search_posts_combined_filters(mock_db):
    user = _user()
    db = mock_db()

    with (
        patch("apps.search.repositories.count_search_posts", AsyncMock(return_value=0)) as count_posts,
        patch("apps.search.repositories.search_posts_with_details", AsyncMock(return_value=[])) as search_posts_repo,
    ):
        await search_posts(
            user,
            db,
            query="robotics",
            hashtag="ai",
            academic_interest="Artificial Intelligence",
            university_name="Tech",
            major="CS",
            minor="Math",
            country="Canada",
            edu_level="1",
            page=3,
            page_size=5,
        )

    kwargs = count_posts.await_args.kwargs
    assert kwargs["query"] == "robotics"
    assert kwargs["hashtag"] == "ai"
    assert kwargs["academic_interest"] == "Artificial Intelligence"
    assert kwargs["university_name"] == "Tech"
    assert kwargs["major"] == "CS"
    assert kwargs["minor"] == "Math"
    assert kwargs["country"] == "Canada"
    assert kwargs["edu_level"] == "1"
    assert search_posts_repo.await_args.kwargs["offset"] == 10
    assert search_posts_repo.await_args.kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_search_posts_private_visibility_uses_repository(mock_db):
    user = _user()
    db = mock_db()

    with (
        patch("apps.search.repositories.count_search_posts", AsyncMock(return_value=0)) as count_posts,
        patch("apps.search.repositories.search_posts_with_details", AsyncMock(return_value=[])) as search_posts_repo,
    ):
        await search_posts(user, db)

    count_posts.assert_not_called()
    search_posts_repo.assert_awaited_once_with(
        db,
        user.id,
        query=None,
        hashtag=None,
        academic_interest=None,
        university_name=None,
        major=None,
        minor=None,
        country=None,
        edu_level=None,
        offset=0,
        limit=None,
    )


@pytest.mark.asyncio
async def test_search_posts_pagination(mock_db):
    user = _user()
    db = mock_db()

    with (
        patch("apps.search.repositories.count_search_posts", AsyncMock(return_value=42)),
        patch("apps.search.repositories.search_posts_with_details", AsyncMock(return_value=[])),
    ):
        result = await search_posts(user, db, page=3, page_size=10)

    assert result == {
        "items": [],
        "page": 3,
        "pageSize": 10,
        "totalItems": 42,
        "totalPages": 5,
    }


@pytest.mark.asyncio
async def test_search_posts_without_pagination_returns_all(mock_db):
    user = _user()
    db = mock_db()

    with (
        patch("apps.search.repositories.count_search_posts", AsyncMock(return_value=42)) as count_posts,
        patch("apps.search.repositories.search_posts_with_details", AsyncMock(return_value=[])) as search_posts_repo,
    ):
        result = await search_posts(user, db)

    count_posts.assert_not_called()
    assert search_posts_repo.await_args.kwargs["limit"] is None
    assert result == {
        "items": [],
        "page": 1,
        "pageSize": 0,
        "totalItems": 0,
        "totalPages": 0,
    }
