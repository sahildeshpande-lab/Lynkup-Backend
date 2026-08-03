from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from apps.engagement.services import post_reactions_list_service as svc
from common.enums import ReactionType


def _profile(**kwargs):
    defaults = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "first_name": "John",
        "last_name": "Doe",
        "major": "CS",
        "minor": "",
        "edu_level": "Bachelors",
        "profile_photo_url": "photo.jpg",
        "bio": "Student developer",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _university(name: str = "Test University"):
    return SimpleNamespace(name=name)


def _reaction(
    reaction_type: ReactionType = ReactionType.like,
    *,
    created_at: datetime | None = None,
):
    return SimpleNamespace(
        reaction_type=reaction_type,
        created_at=created_at or datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_get_post_reactions_post_not_found(mock_db):
    post_id = uuid.uuid4()
    db = mock_db()

    with patch.object(svc, "post_exists", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc:
            await svc.get_post_reactions(db, post_id)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Post not found"


@pytest.mark.asyncio
async def test_get_post_reactions_no_reactions(mock_db):
    post_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "fetch_reaction_summary_counts", AsyncMock(return_value={})),
        patch.object(svc, "fetch_post_reactors", AsyncMock(return_value=[])),
    ):
        response = await svc.get_post_reactions(db, post_id)

    assert response.status is True
    assert response.message == "Reactions fetched successfully"
    assert response.data.reactions["LIKE"] == []
    assert response.data.reactions["CELEBRATE"] == []
    assert response.data.totalItems == 0
    assert response.data.page == 1
    assert response.data.pageSize == 0
    assert response.data.totalPages == 0

    summary = {item.reaction_type: item.count for item in response.data.summary}
    for reaction_type in ReactionType:
        assert summary[reaction_type.value.upper()] == 0


@pytest.mark.asyncio
async def test_get_post_reactions_multiple_types_and_summary(mock_db):
    post_id = uuid.uuid4()
    db = mock_db()
    profile = _profile()
    university = _university()

    summary_counts = {
        ReactionType.like: 59,
        ReactionType.celebrate: 1,
        ReactionType.curious: 1,
    }
    rows = [
        (_reaction(ReactionType.like, created_at=datetime(2026, 7, 10, 14, 0, 0, tzinfo=timezone.utc)), profile, university),
        (_reaction(ReactionType.celebrate, created_at=datetime(2026, 7, 10, 13, 0, 0, tzinfo=timezone.utc)), profile, university),
        (_reaction(ReactionType.curious, created_at=datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)), profile, university),
    ]

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "fetch_reaction_summary_counts", AsyncMock(return_value=summary_counts)),
        patch.object(svc, "fetch_post_reactors", AsyncMock(return_value=rows)),
        patch("apps.engagement.services.author_service.generate_profile_image_url", return_value="https://cdn.example/photo.jpg"),
    ):
        response = await svc.get_post_reactions(db, post_id)

    summary = {item.reaction_type: item.count for item in response.data.summary}
    assert summary["LIKE"] == 59
    assert summary["CELEBRATE"] == 1
    assert summary["CURIOUS"] == 1
    assert summary["INSIGHTFUL"] == 0
    assert summary["SUPPORT"] == 0
    assert len(response.data.reactions["LIKE"]) == 1
    assert len(response.data.reactions["CELEBRATE"]) == 1
    assert len(response.data.reactions["CURIOUS"]) == 1
    assert response.data.reactions["LIKE"][0].reaction_type == "LIKE"
    assert response.data.reactions["LIKE"][0].profile_id == profile.user_id
    assert response.data.reactions["LIKE"][0].profilePhoto_url == "https://cdn.example/photo.jpg"
    assert response.data.reactions["LIKE"][0].bio == "Student developer"
    assert response.data.totalItems == 3


@pytest.mark.asyncio
async def test_get_post_reactions_filter_by_reaction_type(mock_db):
    post_id = uuid.uuid4()
    db = mock_db()
    profile = _profile()
    like_rows = [(_reaction(ReactionType.like), profile, _university())]

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "fetch_reaction_summary_counts", AsyncMock(return_value={
            ReactionType.like: 5,
            ReactionType.celebrate: 2,
        })),
        patch.object(svc, "count_post_reactions", AsyncMock(return_value=5)) as count_reactions,
        patch.object(svc, "fetch_post_reactors", AsyncMock(return_value=like_rows)) as fetch_reactors,
        patch("apps.engagement.services.author_service.generate_profile_image_url", return_value=None),
    ):
        response = await svc.get_post_reactions(
            db,
            post_id,
            reaction_type=ReactionType.like,
            page=1,
            page_size=20,
        )

    count_reactions.assert_awaited_once_with(db, post_id, ReactionType.like)
    fetch_reactors.assert_awaited_once_with(
        db,
        post_id,
        reaction_type=ReactionType.like,
        offset=0,
        limit=20,
    )
    assert response.data.totalItems == 5
    assert set(response.data.reactions.keys()) == {"LIKE"}
    assert len(response.data.reactions["LIKE"]) == 1
    summary = {item.reaction_type: item.count for item in response.data.summary}
    assert len(summary) == len(ReactionType)
    assert summary["LIKE"] == 5
    assert summary["CELEBRATE"] == 2
    assert summary["INSIGHTFUL"] == 0


@pytest.mark.asyncio
async def test_get_post_reactions_pagination(mock_db):
    post_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "fetch_reaction_summary_counts", AsyncMock(return_value={ReactionType.like: 61})),
        patch.object(svc, "count_post_reactions", AsyncMock(return_value=61)),
        patch.object(svc, "fetch_post_reactors", AsyncMock(return_value=[])) as fetch_reactors,
    ):
        response = await svc.get_post_reactions(db, post_id, page=2, page_size=20)

    fetch_reactors.assert_awaited_once_with(
        db,
        post_id,
        reaction_type=None,
        offset=20,
        limit=20,
    )
    assert response.data.totalItems == 61
    assert response.data.page == 2
    assert response.data.pageSize == 20
    assert response.data.totalPages == 4


@pytest.mark.asyncio
async def test_get_post_reactions_without_pagination_returns_all(mock_db):
    post_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "fetch_reaction_summary_counts", AsyncMock(return_value={})),
        patch.object(svc, "count_post_reactions", AsyncMock(return_value=0)) as count_reactions,
        patch.object(svc, "fetch_post_reactors", AsyncMock(return_value=[])) as fetch_reactors,
    ):
        response = await svc.get_post_reactions(db, post_id)

    count_reactions.assert_not_called()
    fetch_reactors.assert_awaited_once_with(
        db,
        post_id,
        reaction_type=None,
        offset=0,
        limit=None,
    )
    assert response.data.pageSize == 0


@pytest.mark.asyncio
async def test_get_post_reactions_ordering_preserved(mock_db):
    post_id = uuid.uuid4()
    db = mock_db()
    profile = _profile()
    university = _university()

    t1 = datetime(2026, 7, 10, 15, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 10, 14, 0, 0, tzinfo=timezone.utc)
    t3 = datetime(2026, 7, 10, 13, 0, 0, tzinfo=timezone.utc)
    rows = [
        (_reaction(ReactionType.like, created_at=t1), profile, university),
        (_reaction(ReactionType.celebrate, created_at=t2), profile, university),
        (_reaction(ReactionType.support, created_at=t3), profile, university),
    ]

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "fetch_reaction_summary_counts", AsyncMock(return_value={})),
        patch.object(svc, "fetch_post_reactors", AsyncMock(return_value=rows)),
        patch("apps.engagement.services.author_service.generate_profile_image_url", return_value=None),
    ):
        response = await svc.get_post_reactions(db, post_id)

    assert response.data.reactions["LIKE"][0].reacted_at == t1
    assert response.data.reactions["CELEBRATE"][0].reacted_at == t2
    assert response.data.reactions["SUPPORT"][0].reacted_at == t3


def test_parse_reaction_type_filter():
    from apps.engagement.schemas import parse_reaction_type_filter

    assert parse_reaction_type_filter(None) is None
    assert parse_reaction_type_filter("LIKE") == ReactionType.like
    assert parse_reaction_type_filter("celebrate") == ReactionType.celebrate

    with pytest.raises(ValueError, match="Invalid reaction_type"):
        parse_reaction_type_filter("LOVE")
