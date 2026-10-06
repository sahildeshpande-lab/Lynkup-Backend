"""Phase 2: raw-SQL feed repost hydration."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.feed.repositories.feed_post_hydration import FeedPostRow, FeedProfileRow
from apps.feed.repositories.feed_combined_hydration import FeedHydrationMaps
from apps.feed.repositories.feed_repost_hydration import (
    FeedRepostRow,
    _REPOST_HYDRATION_SQL,
    _map_repost_rows,
    hydrate_feed_reposts_raw,
)
from apps.feed.services.post_service import format_repost_item


def _repost_sql_row(
    *,
    repost_id,
    profile_user_id,
    created_at="2026-01-02T00:00:00Z",
    **overrides,
):
    base = {
        "repost_id": repost_id,
        "repost_created_at": created_at,
        "profile_user_id": profile_user_id,
        "profile_first_name": "Rep",
        "profile_last_name": "Oster",
        "profile_photo_url": None,
        "profile_visibility": "public",
        "profile_university_id": None,
        "profile_interests_id": [],
        "profile_bio": "r-bio",
        "profile_major": "Math",
        "profile_minor": None,
        "profile_edu_level": "grad",
    }
    base.update(overrides)
    return base


def test_map_one_repost():
    rid = uuid.uuid4()
    uid = uuid.uuid4()
    mapped = _map_repost_rows([_repost_sql_row(repost_id=rid, profile_user_id=uid)])
    assert list(mapped) == [rid]
    repost, profile = mapped[rid]
    assert isinstance(repost, FeedRepostRow)
    assert isinstance(profile, FeedProfileRow)
    assert repost.id == rid
    assert profile.user_id == uid
    assert profile.first_name == "Rep"


def test_map_multiple_reposts():
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    mapped = _map_repost_rows(
        [
            _repost_sql_row(repost_id=r1, profile_user_id=u1, profile_first_name="A"),
            _repost_sql_row(repost_id=r2, profile_user_id=u2, profile_first_name="B"),
        ]
    )
    assert set(mapped) == {r1, r2}
    assert mapped[r1][1].first_name == "A"
    assert mapped[r2][1].first_name == "B"


def test_missing_repost_row_skipped_by_caller_order():
    present = uuid.uuid4()
    missing = uuid.uuid4()
    uid = uuid.uuid4()
    mapped = _map_repost_rows(
        [_repost_sql_row(repost_id=present, profile_user_id=uid)]
    )
    event_ids = [present, missing, present]
    rebuilt = [rid for rid in event_ids if rid in mapped]
    assert rebuilt == [present, present]


def test_formatter_output_equivalent_dto_vs_orm_like():
    post_id = uuid.uuid4()
    author_id = uuid.uuid4()
    reposter_id = uuid.uuid4()
    repost_id = uuid.uuid4()
    viewer = uuid.uuid4()
    reposted_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    original_post = FeedPostRow(
        id=post_id,
        author_user_id=author_id,
        state="published",
        revision_number=1,
        content={"caption": "orig", "visibility": "public"},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_edited=False,
        like_count=2,
        repost_count=1,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        moderation_notes=None,
        attachments=[],
    )
    original_author = FeedProfileRow(
        user_id=author_id,
        first_name="Orig",
        last_name="Author",
        profile_photo_url=None,
        profile_visibility="public",
        university_id=None,
        profile_interests_id=[],
        bio=None,
        major=None,
        minor=None,
        edu_level=None,
    )
    dto_reposter = FeedProfileRow(
        user_id=reposter_id,
        first_name="Rep",
        last_name="Oster",
        profile_photo_url=None,
        profile_visibility="public",
        university_id=None,
        profile_interests_id=[],
        bio="r-bio",
        major="Math",
        minor=None,
        edu_level="grad",
    )
    orm_reposter = SimpleNamespace(
        user_id=reposter_id,
        first_name="Rep",
        last_name="Oster",
        profile_photo_url=None,
        profile_visibility="public",
    )

    with patch(
        "apps.feed.services.post_service.generate_download_url",
        return_value="https://cdn.example/x",
    ), patch(
        "apps.feed.services.post_service.generate_profile_image_url",
        side_effect=lambda url: url,
    ):
        dto_out = format_repost_item(
            original_post,
            original_author_profile=original_author,
            reposter_profile=dto_reposter,
            repost_id=repost_id,
            reposted_at=reposted_at,
            viewer_user_id=viewer,
        )
        orm_out = format_repost_item(
            original_post,
            original_author_profile=original_author,
            reposter_profile=orm_reposter,
            repost_id=repost_id,
            reposted_at=reposted_at,
            viewer_user_id=viewer,
        )

    assert dto_out == orm_out
    assert dto_out["id"] == repost_id
    assert dto_out["author_user_id"] == reposter_id
    assert dto_out["reposted_data"]["id"] == post_id


@pytest.mark.asyncio
async def test_hydrate_no_repost_ids_short_circuits():
    db = AsyncMock()
    result = await hydrate_feed_reposts_raw(db, set())
    assert result == {}
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_hydrate_binds_repost_ids_via_expanding_param():
    rid = uuid.uuid4()
    uid = uuid.uuid4()
    mapping_result = MagicMock()
    mapping_result.mappings.return_value.all.return_value = [
        _repost_sql_row(repost_id=rid, profile_user_id=uid)
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mapping_result)

    grouped = await hydrate_feed_reposts_raw(db, {rid})
    assert rid in grouped
    assert db.execute.await_count == 1
    _stmt, params = db.execute.await_args.args[0], db.execute.await_args.args[1]
    assert params["repost_ids"] == [rid]
    assert ":repost_ids" in _REPOST_HYDRATION_SQL
    assert str(rid) not in _REPOST_HYDRATION_SQL


@pytest.mark.asyncio
async def test_fetch_feed_posts_preserves_repost_event_order():
    """Repost association follows event order; missing repost skips without reorder."""
    from apps.feed.repositories import feed_repository as repo

    post_a = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
    post_b = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
    repost_1 = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
    repost_2 = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
    repost_missing = uuid.UUID("00000000-0000-0000-0000-0000000000c3")
    author = uuid.uuid4()
    reposter = uuid.uuid4()

    events = [
        {
            "post_id": post_a,
            "repost_id": repost_1,
            "created_at": "t1",
            "event_type": "repost",
            "engagement_score": 10,
        },
        {
            "post_id": post_b,
            "repost_id": repost_missing,
            "created_at": "t2",
            "event_type": "repost",
            "engagement_score": 9,
        },
        {
            "post_id": post_b,
            "repost_id": repost_2,
            "created_at": "t3",
            "event_type": "repost",
            "engagement_score": 8,
        },
    ]

    class _EventsResult:
        def mappings(self):
            return self

        def all(self):
            return events

    async def fake_execute(stmt, params=None):
        return _EventsResult()

    def _post(pid: uuid.UUID) -> FeedPostRow:
        return FeedPostRow(
            id=pid,
            author_user_id=author,
            state="published",
            revision_number=1,
            content={},
            created_at="t",
            updated_at="t",
            is_edited=False,
            like_count=0,
            repost_count=0,
            share_count=0,
            comment_count=0,
            is_moderator_reviewed=False,
            reviewed_at=None,
            moderator_id=None,
            moderation_notes=None,
        )

    author_profile = FeedProfileRow(
        user_id=author,
        first_name="A",
        last_name="B",
        profile_photo_url=None,
        profile_visibility="public",
        university_id=None,
        profile_interests_id=[],
        bio=None,
        major=None,
        minor=None,
        edu_level=None,
    )
    posts_map = {
        post_a: (_post(post_a), author_profile),
        post_b: (_post(post_b), author_profile),
    }
    reposter_profile = FeedProfileRow(
        user_id=reposter,
        first_name="R",
        last_name="P",
        profile_photo_url=None,
        profile_visibility="public",
        university_id=None,
        profile_interests_id=[],
        bio=None,
        major=None,
        minor=None,
        edu_level=None,
    )
    # Map deliberately unordered vs events.
    reposts_map = {
        repost_2: (FeedRepostRow(id=repost_2, created_at="t3"), reposter_profile),
        repost_1: (FeedRepostRow(id=repost_1, created_at="t1"), reposter_profile),
    }

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=fake_execute)

    with patch.object(
        repo,
        "hydrate_feed_posts_and_reposts_raw",
        AsyncMock(
            return_value=FeedHydrationMaps(posts=posts_map, reposts=reposts_map)
        ),
    ) as hydrate_combined:
        results, next_cursor = await repo.fetch_feed_posts(
            db,
            uuid.uuid4(),
            None,
            set(),
            cursor=None,
            limit=10,
        )

    hydrate_combined.assert_awaited_once()
    assert next_cursor is None
    assert [item["repost_id"] for item in results] == [repost_1, repost_2]
    assert [item["post"].id for item in results] == [post_a, post_b]
