from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.feed.perf.hydrate_timeline_perf import identify_hydrate_query
from apps.feed.repositories.post_repository import (
    AuthoredTimelineItem,
    RepostTimelineItem,
    UserTimelineEvent,
    hydrate_user_posts_timeline,
)
from apps.feed.repositories.timeline_post_hydration import (
    group_timeline_hydration_rows,
    hydrate_timeline_posts_raw,
)
from apps.feed.services.post_service import format_post_detail


def _post_row(
    post_id: uuid.UUID,
    *,
    author_user_id: uuid.UUID | None = None,
    profile_user_id: uuid.UUID | None = None,
    attachment_id: uuid.UUID | None = None,
    media_id: uuid.UUID | None = None,
    media_key: str = "media/key",
    moderator_email: str | None = None,
    moderator_first: str | None = None,
    moderator_last: str | None = None,
    moderator_id: uuid.UUID | None = None,
) -> dict:
    author_user_id = author_user_id or uuid.uuid4()
    return {
        "post_id": post_id,
        "post_author_user_id": author_user_id,
        "post_state": "published",
        "post_revision_number": 1,
        "post_content": {"caption": "hello", "visibility": "public"},
        "post_created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "post_updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "post_is_edited": False,
        "post_like_count": 3,
        "post_repost_count": 1,
        "post_share_count": 0,
        "post_comment_count": 2,
        "post_is_moderator_reviewed": True,
        "post_reviewed_at": datetime(2026, 1, 3, tzinfo=timezone.utc),
        "post_moderator_id": moderator_id,
        "post_moderation_notes": "ok",
        "profile_user_id": profile_user_id,
        "profile_first_name": "Ada",
        "profile_last_name": "Lovelace",
        "profile_photo_url": "photos/ada.jpg",
        "profile_visibility": "public",
        "profile_university_id": uuid.uuid4(),
        "profile_interests_id": [1, 2],
        "profile_bio": "bio",
        "profile_major": "CS",
        "profile_minor": "Math",
        "profile_edu_level": "Bachelors",
        "moderator_user_email": moderator_email,
        "moderator_profile_first_name": moderator_first,
        "moderator_profile_last_name": moderator_last,
        "attachment_id": attachment_id,
        "media_id": media_id,
        "media_key": media_key,
        "media_type": "image",
        "media_original_filename": "a.png",
        "media_mime_type": "image/png",
        "media_file_size": 100,
    }


def test_identify_combined_timeline_raw_sql_as_main_bucket() -> None:
    bucket, operation, _source = identify_hydrate_query(
        "SELECT p.id FROM posts p "
        "LEFT JOIN post_attachments pa ON pa.post_id = p.id "
        "WHERE p.id IN ($1)"
    )
    assert bucket == "hydrate_posts_main"
    assert operation == "timeline_raw_post_hydration"


def test_group_zero_attachments_returns_post_with_empty_media() -> None:
    post_id = uuid.uuid4()
    profile_user_id = uuid.uuid4()
    grouped = group_timeline_hydration_rows(
        [_post_row(post_id, profile_user_id=profile_user_id, attachment_id=None)]
    )
    post, profile, mod_user, mod_profile = grouped[post_id]
    assert post.attachments == []
    assert profile is not None
    assert profile.user_id == profile_user_id
    assert mod_user is None
    assert mod_profile is None


def test_group_missing_author_profile() -> None:
    post_id = uuid.uuid4()
    grouped = group_timeline_hydration_rows(
        [_post_row(post_id, profile_user_id=None, attachment_id=None)]
    )
    post, profile, _, _ = grouped[post_id]
    assert post.id == post_id
    assert profile is None


def test_group_single_attachment() -> None:
    post_id = uuid.uuid4()
    attachment_id = uuid.uuid4()
    media_id = uuid.uuid4()
    grouped = group_timeline_hydration_rows(
        [
            _post_row(
                post_id,
                profile_user_id=uuid.uuid4(),
                attachment_id=attachment_id,
                media_id=media_id,
            )
        ]
    )
    post, _, _, _ = grouped[post_id]
    assert len(post.attachments) == 1
    assert post.attachments[0].id == attachment_id
    assert post.attachments[0].media_asset is not None
    assert post.attachments[0].media_asset.id == media_id


def test_group_multiple_attachments_sorted_by_attachment_id() -> None:
    post_id = uuid.uuid4()
    a1, a2, a3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [
        _post_row(post_id, profile_user_id=uuid.uuid4(), attachment_id=a2, media_id=uuid.uuid4()),
        _post_row(post_id, profile_user_id=uuid.uuid4(), attachment_id=a1, media_id=uuid.uuid4()),
        _post_row(post_id, profile_user_id=uuid.uuid4(), attachment_id=a3, media_id=uuid.uuid4()),
    ]
    grouped = group_timeline_hydration_rows(rows)
    post, _, _, _ = grouped[post_id]
    assert [attachment.id for attachment in post.attachments] == sorted([a1, a2, a3])


def test_group_moderator_profile_and_email_fallback_fields() -> None:
    post_id = uuid.uuid4()
    mod_id = uuid.uuid4()
    grouped = group_timeline_hydration_rows(
        [
            _post_row(
                post_id,
                profile_user_id=uuid.uuid4(),
                moderator_id=mod_id,
                moderator_email="mod@example.com",
                moderator_first="Mod",
                moderator_last="Erator",
                attachment_id=None,
            )
        ]
    )
    post, author_profile, mod_user, mod_profile = grouped[post_id]
    assert post.moderator_id == mod_id
    assert mod_user is not None
    assert mod_user.email == "mod@example.com"
    assert mod_profile is not None
    assert mod_profile.first_name == "Mod"
    assert mod_profile.last_name == "Erator"

    formatted = format_post_detail(
        post,
        author_profile=author_profile,
        moderator_user=mod_user,
        moderator_profile=mod_profile,
    )
    assert formatted["moderator_name"] == "Mod Erator"


def test_group_moderator_email_fallback_when_profile_name_missing() -> None:
    post_id = uuid.uuid4()
    grouped = group_timeline_hydration_rows(
        [
            _post_row(
                post_id,
                profile_user_id=uuid.uuid4(),
                moderator_id=uuid.uuid4(),
                moderator_email="mod@example.com",
                moderator_first=None,
                moderator_last=None,
                attachment_id=None,
            )
        ]
    )
    post, author_profile, mod_user, mod_profile = grouped[post_id]
    assert mod_profile is None
    formatted = format_post_detail(
        post,
        author_profile=author_profile,
        moderator_user=mod_user,
        moderator_profile=mod_profile,
    )
    assert formatted["moderator_name"] == "mod@example.com"


@pytest.mark.asyncio
async def test_hydrate_timeline_posts_raw_single_sql_round_trip() -> None:
    post_id = uuid.uuid4()
    mapping = MagicMock()
    mapping.mappings.return_value.all.return_value = [
        _post_row(post_id, profile_user_id=uuid.uuid4(), attachment_id=None)
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mapping)

    grouped = await hydrate_timeline_posts_raw(db, [post_id])
    assert db.execute.await_count == 1
    assert post_id in grouped


@pytest.mark.asyncio
async def test_hydrate_user_posts_timeline_empty_events() -> None:
    db = AsyncMock()
    items = await hydrate_user_posts_timeline(db, [], reposter_user_id=uuid.uuid4())
    assert items == []
    db.execute.assert_not_called()


def _repost_hydration_row(
    repost_id: uuid.UUID,
    *,
    reposter_user_id: uuid.UUID,
    created_at: datetime | None = None,
) -> dict:
    return {
        "repost_id": repost_id,
        "repost_created_at": created_at or datetime(2026, 2, 1, tzinfo=timezone.utc),
        "profile_user_id": reposter_user_id,
        "profile_first_name": "Re",
        "profile_last_name": "Poster",
        "profile_photo_url": None,
        "profile_visibility": "public",
        "profile_university_id": None,
        "profile_interests_id": [],
        "profile_bio": None,
        "profile_major": None,
        "profile_minor": None,
        "profile_edu_level": None,
    }


@pytest.mark.asyncio
async def test_hydrate_user_posts_timeline_authored_only_skips_repost_query() -> None:
    post_id = uuid.uuid4()
    mapping = MagicMock()
    mapping.mappings.return_value.all.return_value = [
        _post_row(post_id, profile_user_id=uuid.uuid4(), attachment_id=None)
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mapping)

    events = [
        UserTimelineEvent(
            post_id=post_id,
            repost_id=None,
            event_type="post",
            sort_at=datetime.now(timezone.utc),
        )
    ]
    items = await hydrate_user_posts_timeline(db, events, reposter_user_id=uuid.uuid4())

    assert db.execute.await_count == 1
    assert len(items) == 1
    assert items[0].kind == "post"


@pytest.mark.asyncio
async def test_hydrate_user_posts_timeline_preserves_event_order_authored_and_repost() -> None:
    post_a = uuid.uuid4()
    post_b = uuid.uuid4()
    repost_id = uuid.uuid4()
    reposter_user_id = uuid.uuid4()
    profile_user_id = uuid.uuid4()

    raw_rows = [
        _post_row(post_a, profile_user_id=profile_user_id, attachment_id=None),
        _post_row(post_b, profile_user_id=uuid.uuid4(), attachment_id=None),
    ]

    call_count = 0

    async def _execute(stmt, params=None):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.mappings.return_value.all.return_value = raw_rows
            return result
        result.mappings.return_value.all.return_value = [
            _repost_hydration_row(repost_id, reposter_user_id=reposter_user_id)
        ]
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)

    events = [
        UserTimelineEvent(post_id=post_b, repost_id=None, event_type="post", sort_at=datetime.now(timezone.utc)),
        UserTimelineEvent(post_id=post_a, repost_id=repost_id, event_type="repost", sort_at=datetime.now(timezone.utc)),
        UserTimelineEvent(post_id=post_a, repost_id=None, event_type="post", sort_at=datetime.now(timezone.utc)),
    ]

    items = await hydrate_user_posts_timeline(db, events, reposter_user_id=reposter_user_id)

    assert db.execute.await_count == 2
    assert len(items) == 3
    assert items[0].kind == "post"
    assert items[0].post.id == post_b
    assert items[1].kind == "repost"
    assert isinstance(items[1], RepostTimelineItem)
    assert items[1].repost.id == repost_id
    assert items[1].reposter_profile.user_id == reposter_user_id
    assert items[2].kind == "post"
    assert isinstance(items[2], AuthoredTimelineItem)
    assert items[2].post.id == post_a


@pytest.mark.asyncio
async def test_hydrate_user_posts_timeline_multiple_reposts_single_combined_query() -> None:
    post_a = uuid.uuid4()
    post_b = uuid.uuid4()
    repost_id_1 = uuid.uuid4()
    repost_id_2 = uuid.uuid4()
    reposter_user_id = uuid.uuid4()

    raw_rows = [
        _post_row(post_a, profile_user_id=uuid.uuid4(), attachment_id=None),
        _post_row(post_b, profile_user_id=uuid.uuid4(), attachment_id=None),
    ]

    call_count = 0

    async def _execute(stmt, params=None):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.mappings.return_value.all.return_value = raw_rows
            return result
        result.mappings.return_value.all.return_value = [
            _repost_hydration_row(repost_id_1, reposter_user_id=reposter_user_id),
            _repost_hydration_row(repost_id_2, reposter_user_id=reposter_user_id),
        ]
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)

    events = [
        UserTimelineEvent(post_id=post_a, repost_id=repost_id_1, event_type="repost", sort_at=datetime.now(timezone.utc)),
        UserTimelineEvent(post_id=post_b, repost_id=repost_id_2, event_type="repost", sort_at=datetime.now(timezone.utc)),
    ]

    items = await hydrate_user_posts_timeline(db, events, reposter_user_id=reposter_user_id)

    assert db.execute.await_count == 2
    assert len(items) == 2
    assert items[0].repost.id == repost_id_1
    assert items[1].repost.id == repost_id_2


@pytest.mark.asyncio
async def test_hydrate_user_posts_timeline_repost_of_own_post_uses_reposter_profile() -> None:
    post_id = uuid.uuid4()
    repost_id = uuid.uuid4()
    user_id = uuid.uuid4()

    raw_rows = [_post_row(post_id, profile_user_id=user_id, attachment_id=None)]

    call_count = 0

    async def _execute(stmt, params=None):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.mappings.return_value.all.return_value = raw_rows
            return result
        result.mappings.return_value.all.return_value = [
            _repost_hydration_row(repost_id, reposter_user_id=user_id)
        ]
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)

    events = [
        UserTimelineEvent(
            post_id=post_id,
            repost_id=repost_id,
            event_type="repost",
            sort_at=datetime.now(timezone.utc),
        )
    ]

    items = await hydrate_user_posts_timeline(db, events, reposter_user_id=user_id)

    assert len(items) == 1
    assert items[0].author_profile.user_id == user_id
    assert items[0].reposter_profile.user_id == user_id


@pytest.mark.asyncio
async def test_hydrate_user_posts_timeline_missing_repost_skips_event() -> None:
    post_id = uuid.uuid4()
    missing_repost_id = uuid.uuid4()

    mapping = MagicMock()
    mapping.mappings.return_value.all.return_value = [
        _post_row(post_id, profile_user_id=uuid.uuid4(), attachment_id=None)
    ]

    call_count = 0

    async def _execute(stmt, params=None):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.mappings.return_value.all.return_value = mapping.mappings.return_value.all.return_value
            return result
        result.mappings.return_value.all.return_value = []
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)

    events = [
        UserTimelineEvent(
            post_id=post_id,
            repost_id=missing_repost_id,
            event_type="repost",
            sort_at=datetime.now(timezone.utc),
        )
    ]

    items = await hydrate_user_posts_timeline(db, events, reposter_user_id=uuid.uuid4())

    assert items == []
