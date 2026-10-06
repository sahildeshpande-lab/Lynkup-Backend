"""Phase 1: raw-SQL feed post hydration (grouping, order, formatter parity)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.feed.repositories.feed_combined_hydration import FeedHydrationMaps
from apps.feed.repositories.feed_post_hydration import (
    FeedAttachmentRow,
    FeedMediaAssetRow,
    FeedPostRow,
    FeedProfileRow,
    _POST_HYDRATION_SQL,
    _group_hydration_rows,
    hydrate_feed_posts_raw,
)
from apps.feed.services.post_service import format_post_detail


def _media_row(
    *,
    post_id,
    profile_user_id,
    attachment_id=None,
    media_id=None,
    media_key=None,
    **overrides,
):
    base = {
        "post_id": post_id,
        "post_author_user_id": profile_user_id,
        "post_state": "published",
        "post_revision_number": 1,
        "post_content": {"caption": "c", "visibility": "public"},
        "post_created_at": "2026-01-01T00:00:00Z",
        "post_updated_at": "2026-01-01T00:00:00Z",
        "post_is_edited": False,
        "post_like_count": 1,
        "post_repost_count": 0,
        "post_share_count": 0,
        "post_comment_count": 2,
        "post_is_moderator_reviewed": False,
        "post_reviewed_at": None,
        "post_moderator_id": None,
        "post_moderation_notes": None,
        "profile_user_id": profile_user_id,
        "profile_first_name": "Ada",
        "profile_last_name": "Lovelace",
        "profile_photo_url": None,
        "profile_visibility": "public",
        "profile_university_id": None,
        "profile_interests_id": [],
        "profile_bio": "bio",
        "profile_major": "CS",
        "profile_minor": None,
        "profile_edu_level": "undergrad",
        "attachment_id": attachment_id,
        "media_id": media_id,
        "media_key": media_key,
        "media_type": "image" if media_id else None,
        "media_original_filename": "a.jpg" if media_id else None,
        "media_mime_type": "image/jpeg" if media_id else None,
        "media_file_size": 10 if media_id else None,
    }
    base.update(overrides)
    return base


def test_group_single_post_without_attachments():
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    grouped = _group_hydration_rows([_media_row(post_id=post_id, profile_user_id=user_id)])
    assert list(grouped) == [post_id]
    post, profile = grouped[post_id]
    assert isinstance(post, FeedPostRow)
    assert isinstance(profile, FeedProfileRow)
    assert post.attachments == []
    assert profile.first_name == "Ada"
    assert post.content["caption"] == "c"


def test_group_single_post_with_multiple_attachments_ordered_by_id():
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    att_b = uuid.UUID("00000000-0000-0000-0000-000000000002")
    att_a = uuid.UUID("00000000-0000-0000-0000-000000000001")
    media_b = uuid.uuid4()
    media_a = uuid.uuid4()
    # Intentionally out of attachment-id order in the input rows.
    rows = [
        _media_row(
            post_id=post_id,
            profile_user_id=user_id,
            attachment_id=att_b,
            media_id=media_b,
            media_key="b",
        ),
        _media_row(
            post_id=post_id,
            profile_user_id=user_id,
            attachment_id=att_a,
            media_id=media_a,
            media_key="a",
        ),
    ]
    post, _profile = _group_hydration_rows(rows)[post_id]
    assert [a.id for a in post.attachments] == [att_a, att_b]
    assert [a.media_asset.key for a in post.attachments] == ["a", "b"]


def test_group_multiple_posts_and_no_media():
    p1, p2 = uuid.uuid4(), uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    grouped = _group_hydration_rows(
        [
            _media_row(post_id=p1, profile_user_id=u1),
            _media_row(post_id=p2, profile_user_id=u2),
        ]
    )
    assert set(grouped) == {p1, p2}
    assert grouped[p1][0].attachments == []
    assert grouped[p2][0].attachments == []


def test_feed_order_preserved_via_id_map():
    """Hydration map must be re-ordered by caller using original post_ids."""
    id_42 = uuid.UUID("00000000-0000-0000-0000-000000000042")
    id_17 = uuid.UUID("00000000-0000-0000-0000-000000000017")
    id_91 = uuid.UUID("00000000-0000-0000-0000-000000000091")
    u = uuid.uuid4()
    # SQL returns posts in a different order than the feed event list.
    grouped = _group_hydration_rows(
        [
            _media_row(post_id=id_91, profile_user_id=u),
            _media_row(post_id=id_42, profile_user_id=u),
            _media_row(post_id=id_17, profile_user_id=u),
        ]
    )
    feed_order = [id_42, id_17, id_91]
    rebuilt = [grouped[pid][0].id for pid in feed_order if pid in grouped]
    assert rebuilt == feed_order


def test_missing_hydration_row_does_not_reorder_feed():
    id_a = uuid.uuid4()
    id_missing = uuid.uuid4()
    id_c = uuid.uuid4()
    u = uuid.uuid4()
    grouped = _group_hydration_rows(
        [
            _media_row(post_id=id_a, profile_user_id=u),
            _media_row(post_id=id_c, profile_user_id=u),
        ]
    )
    feed_order = [id_a, id_missing, id_c]
    rebuilt = [pid for pid in feed_order if pid in grouped]
    assert rebuilt == [id_a, id_c]


def test_formatter_output_equivalent_for_dto_vs_orm_like_namespace():
    post_id = uuid.uuid4()
    author_id = uuid.uuid4()
    media_id = uuid.uuid4()
    att_id = uuid.uuid4()

    dto_post = FeedPostRow(
        id=post_id,
        author_user_id=author_id,
        state="published",
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hi</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        is_edited=False,
        like_count=3,
        repost_count=1,
        share_count=0,
        comment_count=4,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        moderation_notes=None,
        attachments=[
            FeedAttachmentRow(
                id=att_id,
                media_asset=FeedMediaAssetRow(
                    id=media_id,
                    key="k1",
                    type="image",
                    original_filename="x.jpg",
                    mime_type="image/jpeg",
                    file_size=99,
                ),
            )
        ],
    )
    dto_profile = FeedProfileRow(
        user_id=author_id,
        first_name="Ada",
        last_name="Lovelace",
        profile_photo_url=None,
        profile_visibility="public",
        university_id=None,
        profile_interests_id=[],
        bio="bio",
        major="CS",
        minor=None,
        edu_level="undergrad",
    )

    orm_like = SimpleNamespace(
        id=post_id,
        author_user_id=author_id,
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hi</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        is_edited=False,
        like_count=3,
        repost_count=1,
        share_count=0,
        comment_count=4,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        moderation_notes=None,
        attachments=[
            SimpleNamespace(
                id=att_id,
                media_asset=SimpleNamespace(
                    id=media_id,
                    key="k1",
                    type="image",
                    original_filename="x.jpg",
                    mime_type="image/jpeg",
                    file_size=99,
                ),
            )
        ],
    )
    orm_profile = SimpleNamespace(
        first_name="Ada",
        last_name="Lovelace",
        profile_photo_url=None,
        profile_visibility="public",
    )

    with patch(
        "apps.feed.services.post_service.generate_download_url",
        return_value="https://cdn.example/k1",
    ), patch(
        "apps.feed.services.post_service.generate_profile_image_url",
        side_effect=lambda url: url,
    ):
        dto_out = format_post_detail(dto_post, author_profile=dto_profile)
        orm_out = format_post_detail(orm_like, author_profile=orm_profile)

    assert dto_out == orm_out
    assert len(dto_out["media"]) == 1
    assert dto_out["media"][0]["key"] == "k1"


@pytest.mark.asyncio
async def test_hydrate_empty_post_ids_short_circuits():
    db = AsyncMock()
    result = await hydrate_feed_posts_raw(db, set())
    assert result == {}
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_hydrate_binds_post_ids_via_expanding_param():
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mapping_result = MagicMock()
    mapping_result.mappings.return_value.all.return_value = [
        _media_row(post_id=post_id, profile_user_id=user_id)
    ]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=mapping_result)

    grouped = await hydrate_feed_posts_raw(db, {post_id})
    assert post_id in grouped

    assert db.execute.await_count == 1
    stmt, params = db.execute.await_args.args[0], db.execute.await_args.args[1]
    assert "post_ids" in params
    assert params["post_ids"] == [post_id]
    # Expanding bind — never string-interpolated ID list into SQL text.
    assert ":post_ids" in _POST_HYDRATION_SQL
    assert str(post_id) not in _POST_HYDRATION_SQL
    compiled = str(stmt)
    assert "IN" in compiled.upper() or "post_ids" in compiled


@pytest.mark.asyncio
async def test_fetch_feed_posts_rebuilds_event_order():
    """fetch_feed_posts must emit results in event order, not hydration map order."""
    from apps.feed.repositories import feed_repository as repo

    id_42 = uuid.UUID("00000000-0000-0000-0000-000000000042")
    id_17 = uuid.UUID("00000000-0000-0000-0000-000000000017")
    id_91 = uuid.UUID("00000000-0000-0000-0000-000000000091")
    u = uuid.uuid4()

    events = [
        {
            "post_id": id_42,
            "repost_id": None,
            "created_at": "t1",
            "event_type": "post",
            "engagement_score": 10,
        },
        {
            "post_id": id_17,
            "repost_id": None,
            "created_at": "t2",
            "event_type": "post",
            "engagement_score": 9,
        },
        {
            "post_id": id_91,
            "repost_id": None,
            "created_at": "t3",
            "event_type": "post",
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

    # Return map deliberately shuffled vs feed order.
    hydration_map = {
        id_91: (
            FeedPostRow(
                id=id_91,
                author_user_id=u,
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
            ),
            FeedProfileRow(
                user_id=u,
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
            ),
        ),
        id_42: (
            FeedPostRow(
                id=id_42,
                author_user_id=u,
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
            ),
            FeedProfileRow(
                user_id=u,
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
            ),
        ),
        id_17: (
            FeedPostRow(
                id=id_17,
                author_user_id=u,
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
            ),
            FeedProfileRow(
                user_id=u,
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
            ),
        ),
    }

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=fake_execute)

    with patch.object(
        repo,
        "hydrate_feed_posts_and_reposts_raw",
        AsyncMock(return_value=FeedHydrationMaps(posts=hydration_map, reposts={})),
    ) as hydrate_mock:
        results, next_cursor = await repo.fetch_feed_posts(
            db,
            uuid.uuid4(),
            None,
            set(),
            cursor=None,
            limit=10,
        )

    hydrate_mock.assert_awaited_once()
    assert next_cursor is None
    assert [item["post"].id for item in results] == [id_42, id_17, id_91]
