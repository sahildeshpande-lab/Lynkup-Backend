from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.feed.services.post_service import build_post_detail_response, get_post_service
from common.enums import PostState, ProfileVisibility, UserStatus
from common.exceptions import ApiError


class _RowResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


def _post(*, state: PostState, visibility: str = "public", author_id=None):
    return SimpleNamespace(
        id=uuid4(),
        author_user_id=author_id or uuid4(),
        state=state,
        content={"visibility": visibility},
    )


def _author():
    return SimpleNamespace(
        status=UserStatus.active,
        is_deleted=False,
        deleted_at=None,
    )


def _db_with_post(post, author):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_RowResult((post, author)))
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [PostState.published, PostState.reinstate],
)
async def test_authenticated_user_can_fetch_detail_visible_states(state):
    author_id = uuid4()
    viewer_id = uuid4()
    post = _post(state=state, author_id=author_id)
    db = _db_with_post(post, _author())

    with patch("apps.feed.services.post_service.is_blocked", AsyncMock(return_value=False)):
        result = await get_post_service(post.id, viewer_id, db)

    assert result is post


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [PostState.flagged, PostState.processing, PostState.draft],
)
async def test_other_user_cannot_fetch_non_detail_visible_states(state):
    author_id = uuid4()
    post = _post(state=state, author_id=author_id)
    db = _db_with_post(post, _author())

    with pytest.raises(ApiError) as exc_info:
        await get_post_service(post.id, uuid4(), db)
    assert "not accessible" in exc_info.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [PostState.flagged, PostState.processing],
)
async def test_author_can_fetch_own_flagged_or_processing_post(state):
    author_id = uuid4()
    post = _post(state=state, author_id=author_id)
    db = _db_with_post(post, _author())

    result = await get_post_service(post.id, author_id, db)
    assert result is post


@pytest.mark.asyncio
async def test_regular_user_cannot_fetch_private_flagged_post():
    author_id = uuid4()
    post = _post(state=PostState.flagged, visibility="private", author_id=author_id)
    db = _db_with_post(post, _author())

    with patch("apps.feed.services.post_service.is_blocked", AsyncMock(return_value=False)):
        with pytest.raises(ApiError) as exc_info:
            await get_post_service(post.id, uuid4(), db)
    assert "not accessible" in exc_info.value.message


@pytest.mark.asyncio
async def test_deleted_post_hidden_from_all_non_staff_viewers():
    author_id = uuid4()
    post = _post(state=PostState.deleted, author_id=author_id)
    db = _db_with_post(post, _author())

    with pytest.raises(ApiError) as exc_info:
        await get_post_service(post.id, author_id, db)
    assert "not found" in exc_info.value.message.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["moderator", "viewer", "superadmin"])
async def test_staff_roles_can_fetch_deleted_post(role):
    post = _post(state=PostState.deleted)
    db = _db_with_post(post, _author())

    result = await get_post_service(post.id, uuid4(), db, viewer_role=role)
    assert result is post


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["moderator", "viewer", "superadmin"])
async def test_staff_roles_can_fetch_private_flagged_post(role):
    author_id = uuid4()
    post = _post(state=PostState.flagged, visibility="private", author_id=author_id)
    db = _db_with_post(post, _author())

    result = await get_post_service(post.id, uuid4(), db, viewer_role=role)
    assert result is post


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["moderator", "viewer", "superadmin"])
@pytest.mark.parametrize("state", [PostState.flagged, PostState.processing, PostState.deleted])
async def test_staff_roles_can_fetch_flagged_processing_deleted(role, state):
    post = _post(state=state)
    db = _db_with_post(post, _author())

    result = await get_post_service(post.id, uuid4(), db, viewer_role=role)
    assert result is post


@pytest.mark.asyncio
async def test_authenticated_user_can_fetch_post_when_author_profile_is_private():
    """GET /posts/{id} is not blocked by the author's account visibility."""
    author_id = uuid4()
    post = _post(state=PostState.published, author_id=author_id)
    db = _db_with_post(post, _author())

    with patch("apps.feed.services.post_service.is_blocked", AsyncMock(return_value=False)):
        result = await get_post_service(post.id, uuid4(), db)

    assert result is post


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._value


@pytest.mark.asyncio
async def test_build_post_detail_response_includes_full_payload_for_private_profile():
    author_id = uuid4()
    viewer_id = uuid4()
    post = SimpleNamespace(
        id=uuid4(),
        author_user_id=author_id,
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        is_edited=False,
        like_count=5,
        repost_count=2,
        share_count=2,
        comment_count=1,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        moderation_notes=None,
        attachments=[],
    )
    author_profile = SimpleNamespace(
        user_id=author_id,
        first_name="Ada",
        last_name="Lovelace",
        profile_photo_url=None,
        profile_visibility=ProfileVisibility.private,
    )
    author_user = SimpleNamespace(id=author_id, email="ada@example.com", status=UserStatus.active)
    profile_details = {
        author_id: {
            "university": "Test University",
            "university_details": {
                "id": str(uuid4()),
                "university_name": "Test University",
                "university_website": "https://example.edu",
            },
            "bio": "Test",
            "academic_interest": ["Accounting"],
            "major": "Accounting",
            "minor": None,
            "education_level": "MD",
        }
    }

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_ScalarResult(author_profile), _ScalarResult(author_user)])
    flags = SimpleNamespace(
        reposted_post_ids=set(),
        bookmarked_post_ids=set(),
        user_reaction_for=lambda _post_id: None,
    )

    with (
        patch(
            "apps.feed.services.profile_enrichment.load_profile_details",
            AsyncMock(return_value=profile_details),
        ),
        patch(
            "apps.connections.services.recommendation_service.get_user_connections",
            AsyncMock(return_value=set()),
        ),
        patch(
            "apps.feed.services.profile_enrichment.load_requested_user_ids",
            AsyncMock(return_value=set()),
        ),
        patch(
            "apps.engagement.repositories.fetch_post_engagement_flags",
            AsyncMock(return_value=flags),
        ),
        patch(
            "apps.engagement.services.post_reaction_formatters.load_latest_post_reactions",
            AsyncMock(return_value={post.id: {"LIKE": [], "CELEBRATE": [], "INSIGHTFUL": [], "SUPPORT": [], "CURIOUS": []}}),
        ),
        patch(
            "apps.feed.repositories.post_revision_repository.posts_with_triggered_moderation_review",
            AsyncMock(return_value=set()),
        ),
    ):
        data = await build_post_detail_response(db, post, viewer_user_id=viewer_id)

    assert data["profile_visibility"] == "private"
    assert data["first_name"] == "Ada"
    assert data["university"] == "Test University"
    assert data["bio"] == "Test"
    assert data["academic_interest"] == ["Accounting"]
    assert data["major"] == "Accounting"
    assert data["is_connected"] is False
    assert data["is_requested"] is False
    assert data["like_count"] == 5
    assert data["reactions"]["LIKE"] == []
