from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from apps.feed.services.feed_service import get_feed_service
from apps.feed.services.profile_enrichment import (
    load_profile_details as _load_profile_details,
    load_requested_user_ids as _load_requested_user_ids,
)


@pytest.mark.asyncio
async def test_load_requested_user_ids_returns_outgoing_and_incoming_targets():
    current_user_id = uuid.uuid4()
    outgoing_user_id = uuid.uuid4()
    incoming_user_id = uuid.uuid4()
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(
            all=lambda: [
                SimpleNamespace(
                    sender_user_id=current_user_id,
                    receiver_user_id=outgoing_user_id,
                ),
                SimpleNamespace(
                    sender_user_id=incoming_user_id,
                    receiver_user_id=current_user_id,
                ),
            ]
        )
    )

    requested_ids = await _load_requested_user_ids(
        db,
        current_user_id,
        {outgoing_user_id, incoming_user_id, uuid.uuid4()},
    )

    assert requested_ids == {outgoing_user_id, incoming_user_id}
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_profile_details_resolves_names_in_batches():
    user_id = uuid.uuid4()
    university_id = uuid.uuid4()
    profile = SimpleNamespace(
        university_id=university_id,
        bio="Profile bio",
        profile_interests_id=["2", 1, "invalid"],
        major="Computer Science",
        minor="Mathematics",
        edu_level="Masters",
    )
    db = AsyncMock()
    db.execute.side_effect = [
        SimpleNamespace(
            all=lambda: [
                SimpleNamespace(id=university_id, name="Lynkup University")
            ]
        ),
        SimpleNamespace(
            all=lambda: [
                SimpleNamespace(id=1, name="AI"),
                SimpleNamespace(id=2, name="Data Science"),
            ]
        ),
    ]

    details = await _load_profile_details(db, {user_id: profile})

    assert db.execute.await_count == 2
    assert details[user_id] == {
        "university": "Lynkup University",
        "university_details": {
            "id": str(university_id),
            "university_name": "Lynkup University",
            "university_website": None,
        },
        "bio": "Profile bio",
        "academic_interest": ["Data Science", "AI"],
        "major": "Computer Science",
        "minor": "Mathematics",
        "education_level": "Masters",
    }


@pytest.mark.asyncio
async def test_get_feed_service_normal_post():
    current_user_id = uuid.uuid4()
    post_id = uuid.uuid4()
    author_id = uuid.uuid4()

    post = SimpleNamespace(
        id=post_id,
        author_user_id=author_id,
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "Original Caption", "content_html": "<p>Original HTML</p>", "visibility": "public"},
        created_at=datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc),
        like_count=10,
        repost_count=2,
        share_count=1,
        comment_count=5,
        is_moderator_reviewed=True,
        reviewed_at=datetime(2026, 7, 15, 10, 5, 0, tzinfo=timezone.utc),
        moderator_id=uuid.uuid4(),
        attachments=[],
    )

    author_profile = SimpleNamespace(
        user_id=author_id,
        first_name="Alice",
        last_name="Smith",
        profile_photo_url="alice_photo.png",
        profile_visibility="private",
    )

    feed_item = {
        "post": post,
        "author_profile": author_profile,
        "is_reposted": False,
        "repost_id": None,
        "reposted_by_profile": None,
        "reposted_at": None,
    }

    db = AsyncMock()

    with (
        patch("apps.feed.services.feed_service.count_feed_posts", AsyncMock(return_value=1)),
        patch("apps.feed.services.feed_service.fetch_feed_posts", AsyncMock(return_value=([feed_item], None))),
        patch(
            "apps.feed.services.feed_service._load_feed_enrichment_and_user_state",
            AsyncMock(
                return_value=SimpleNamespace(
                    enrichment=SimpleNamespace(
                        profile_details={
                        author_id: {
                            "university": "Lynkup University",
                            "bio": "Student bio",
                            "academic_interest": ["Computer Science"],
                            "major": "Software Engineering",
                            "minor": "Mathematics",
                        }
                    },
                        requested_user_ids={author_id},
                        connected_user_ids={author_id},
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

        results = await get_feed_service(current_user_id, db)

        assert len(results) == 1
        formatted = results[0]
        assert formatted["id"] == post_id
        assert formatted["author_user_id"] == author_id
        assert formatted["first_name"] == "Alice"
        assert formatted["last_name"] == "Smith"
        assert "alice_photo.png" in formatted["profilePhoto_url"]
        assert formatted["profile_visibility"] == "private"
        assert formatted["is_connected"] is True
        assert formatted["is_requested"] is True
        assert formatted["university"] == "Lynkup University"
        assert formatted["bio"] == "Student bio"
        assert formatted["academic_interest"] == ["Computer Science"]
        assert formatted["major"] == "Software Engineering"
        assert formatted["minor"] == "Mathematics"
        assert formatted["is_reposted"] is False
        assert formatted["reposted_data"] is None


@pytest.mark.asyncio
async def test_get_feed_service_repost_item():
    current_user_id = uuid.uuid4()
    post_id = uuid.uuid4()
    author_id = uuid.uuid4()
    reposter_user_id = uuid.uuid4()
    repost_id = uuid.uuid4()
    reposted_at = datetime(2026, 7, 15, 11, 0, 0, tzinfo=timezone.utc)

    post = SimpleNamespace(
        id=post_id,
        author_user_id=author_id,
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "Original Caption", "content_html": "<p>Original HTML</p>", "visibility": "public"},
        created_at=datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc),
        like_count=10,
        repost_count=2,
        share_count=1,
        comment_count=5,
        is_moderator_reviewed=True,
        reviewed_at=datetime(2026, 7, 15, 10, 5, 0, tzinfo=timezone.utc),
        moderator_id=uuid.uuid4(),
        attachments=[],
    )

    author_profile = SimpleNamespace(
        user_id=author_id,
        first_name="Alice",
        last_name="Smith",
        profile_photo_url="alice_photo.png",
        profile_visibility="public",
    )

    reposter_profile = SimpleNamespace(
        user_id=reposter_user_id,
        first_name="Bob",
        last_name="Jones",
        profile_photo_url="bob_photo.png",
        profile_visibility="private",
    )

    feed_item = {
        "post": post,
        "author_profile": author_profile,
        "is_reposted": True,
        "repost_id": repost_id,
        "reposted_by_profile": reposter_profile,
        "reposted_at": reposted_at,
    }

    db = AsyncMock()

    with (
        patch("apps.feed.services.feed_service.count_feed_posts", AsyncMock(return_value=1)),
        patch("apps.feed.services.feed_service.fetch_feed_posts", AsyncMock(return_value=([feed_item], None))),
        patch(
            "apps.feed.services.feed_service._load_feed_enrichment_and_user_state",
            AsyncMock(
                return_value=SimpleNamespace(
                    enrichment=SimpleNamespace(
                        profile_details={
                        author_id: {
                            "university": "Original University",
                            "bio": "Original author bio",
                            "academic_interest": ["Biology"],
                            "major": "Biology",
                            "minor": "Chemistry",
                        },
                        reposter_user_id: {
                            "university": "Reposter University",
                            "bio": "Reposter bio",
                            "academic_interest": ["Design"],
                            "major": "Design",
                            "minor": "Business",
                        },
                    },
                        requested_user_ids={reposter_user_id},
                        connected_user_ids={author_id},
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

        results = await get_feed_service(current_user_id, db)

        assert len(results) == 1
        formatted = results[0]

        # Outer object is the reposter common post
        assert formatted["id"] == repost_id
        assert formatted["author_user_id"] == reposter_user_id
        assert formatted["first_name"] == "Bob"
        assert formatted["last_name"] == "Jones"
        assert "bob_photo.png" in formatted["profilePhoto_url"]
        assert formatted["profile_visibility"] == "private"
        assert formatted["is_connected"] is False
        assert formatted["is_requested"] is True
        assert formatted["university"] == "Reposter University"
        assert formatted["bio"] == "Reposter bio"
        assert formatted["academic_interest"] == ["Design"]
        assert formatted["major"] == "Design"
        assert formatted["minor"] == "Business"
        assert formatted["created_at"] == reposted_at
        assert formatted["is_reposted"] is True
        assert formatted["like_count"] == 10
        assert formatted["repost_count"] == 2
        assert formatted["content"]["caption"] is None
        assert formatted["media"] == []

        # Original post lives only inside reposted_data
        nested = formatted["reposted_data"]
        assert nested is not None
        assert nested["id"] == post_id
        assert nested["author_user_id"] == author_id
        assert nested["first_name"] == "Alice"
        assert nested["last_name"] == "Smith"
        assert "alice_photo.png" in nested["profilePhoto_url"]
        assert nested["profile_visibility"] == "public"
        assert nested["is_connected"] is True
        assert nested["is_requested"] is False
        assert nested["university"] == "Original University"
        assert nested["bio"] == "Original author bio"
        assert nested["academic_interest"] == ["Biology"]
        assert nested["major"] == "Biology"
        assert nested["minor"] == "Chemistry"
        assert nested["content"]["caption"] == "Original Caption"
        assert nested["like_count"] == 10
        assert nested["reposted_data"] is None
        assert "reposted_by" not in formatted


@pytest.mark.asyncio
async def test_get_feed_service_tuples_normalization():
    current_user_id = uuid.uuid4()
    post_id = uuid.uuid4()
    author_id = uuid.uuid4()

    post = SimpleNamespace(
        id=post_id,
        author_user_id=author_id,
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "Original Caption", "content_html": "<p>Original HTML</p>", "visibility": "public"},
        created_at=datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc),
        like_count=10,
        repost_count=2,
        share_count=1,
        comment_count=5,
        is_moderator_reviewed=True,
        reviewed_at=datetime(2026, 7, 15, 10, 5, 0, tzinfo=timezone.utc),
        moderator_id=uuid.uuid4(),
        attachments=[],
    )

    author_profile = SimpleNamespace(
        first_name="Alice",
        last_name="Smith",
        profile_photo_url="alice_photo.png",
    )

    # Return as a tuple of (post, profile) like old fetch_feed_posts output
    db = AsyncMock()

    with (
        patch("apps.feed.services.feed_service.count_feed_posts", AsyncMock(return_value=1)),
        patch("apps.feed.services.feed_service.fetch_feed_posts", AsyncMock(return_value=([(post, author_profile)], None))),
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

        results = await get_feed_service(current_user_id, db)

        assert len(results) == 1
        formatted = results[0]
        assert formatted["id"] == post_id
        assert formatted["first_name"] == "Alice"
        assert formatted["is_reposted"] is False
        assert formatted["reposted_data"] is None


def test_feed_sql_excludes_reposted_self_posts():
    from apps.feed.repositories.feed_repository import _FEED_COUNT_SQL, _FEED_EVENTS_SQL

    # _FEED_EVENTS_SQL must exclude user's own original posts in reposts branch
    assert "p.author_user_id <> :current_user" in _FEED_EVENTS_SQL
    assert "reposter_profile.user_id <> :current_user" in _FEED_EVENTS_SQL

    # Prefer academic matches and connected users; fall back to all visible.
    # Original posts match the author; reposts match the reposter or original author.
    assert "is_match" in _FEED_EVENTS_SQL
    assert "max_match" in _FEED_EVENTS_SQL
    assert "reposter_profile.university_id" in _FEED_EVENTS_SQL
    assert "author_profile.university_id" in _FEED_EVENTS_SQL
    assert "FROM connections c" in _FEED_EVENTS_SQL

    # _FEED_COUNT_SQL must also exclude user's own original posts in reposts branch
    assert "p.author_user_id <> :current_user" in _FEED_COUNT_SQL
    assert "reposter_profile.user_id <> :current_user" in _FEED_COUNT_SQL
    assert "r.is_deleted = false" in _FEED_COUNT_SQL
    assert "is_match" in _FEED_COUNT_SQL
    assert "max_match" in _FEED_COUNT_SQL
    assert "reposter_profile.university_id" in _FEED_COUNT_SQL
    assert "author_profile.university_id" in _FEED_COUNT_SQL
    assert "FROM connections c" in _FEED_COUNT_SQL


def test_feed_match_sql_includes_academic_or_connected():
    from apps.feed.repositories.feed_repository import _feed_match_sql

    post_sql = _feed_match_sql("author_profile", "p.author_user_id")
    assert "author_profile.university_id" in post_sql
    assert "author_profile.major" in post_sql
    assert "author_profile.minor" in post_sql
    assert "FROM connections c" in post_sql
    assert "p.author_user_id" in post_sql

    repost_sql = _feed_match_sql(
        "reposter_profile",
        "reposter_profile.user_id",
        "p.author_user_id",
    )
    assert "reposter_profile.university_id" in repost_sql
    assert "reposter_profile.user_id" in repost_sql
    assert "p.author_user_id" in repost_sql
    assert "FROM connections c" in repost_sql


