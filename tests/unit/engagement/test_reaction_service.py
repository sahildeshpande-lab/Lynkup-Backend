from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from apps.engagement.schemas import UpsertPostReactionRequest
from apps.engagement.services import reaction_service as svc
from common.enums import ReactionType


def _post(like_count: int = 0):
    return SimpleNamespace(id=uuid.uuid4(), like_count=like_count)


def _reaction(reaction_type: ReactionType = ReactionType.like):
    return SimpleNamespace(reaction_type=reaction_type)


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (None, ReactionType.like, 1),
        (ReactionType.like, ReactionType.like, 0),
        (ReactionType.like, None, -1),
        (None, None, 0),
        (None, ReactionType.celebrate, 1),
        (ReactionType.like, ReactionType.celebrate, 0),
        (ReactionType.celebrate, ReactionType.like, 0),
        (ReactionType.insightful, ReactionType.support, 0),
    ],
)
def test_like_count_delta(previous, current, expected):
    assert svc._like_count_delta(previous, current) == expected


def test_format_user_reaction():
    assert svc.format_user_reaction(ReactionType.like) == "LIKE"
    assert svc.format_user_reaction(None) is None


@pytest.mark.asyncio
async def test_upsert_post_reaction_creates_like_and_increments_counter(mock_db):
    post = _post(like_count=4)
    user_id = uuid.uuid4()
    payload = UpsertPostReactionRequest(post_id=post.id, reaction_type="LIKE")
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_user_reaction", AsyncMock(return_value=None)),
        patch.object(svc, "upsert_user_reaction", AsyncMock()) as upsert,
        patch.object(svc, "update_post_like_count", AsyncMock(return_value=5)) as update_count,
    ):
        response = await svc.upsert_post_reaction(db, user_id, payload)

    assert response.status is True
    assert response.message == "Reaction updated successfully"
    assert response.data.like_count == 5
    assert response.data.user_reaction == "LIKE"
    upsert.assert_awaited_once()
    update_count.assert_awaited_once_with(db, post.id, 1)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_post_reaction_same_type_is_idempotent(mock_db):
    post = _post(like_count=10)
    user_id = uuid.uuid4()
    payload = UpsertPostReactionRequest(post_id=post.id, reaction_type="like")
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_user_reaction", AsyncMock(return_value=_reaction(ReactionType.like))),
        patch.object(svc, "upsert_user_reaction", AsyncMock()) as upsert,
        patch.object(svc, "update_post_like_count", AsyncMock()) as update_count,
    ):
        response = await svc.upsert_post_reaction(db, user_id, payload)

    assert response.status is True
    assert response.message == "Reaction updated successfully"
    assert response.data.like_count == 10
    assert response.data.user_reaction == "LIKE"
    upsert.assert_not_called()
    update_count.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_upsert_post_reaction_remove_decrements_like_count(mock_db):
    post = _post(like_count=3)
    user_id = uuid.uuid4()
    payload = UpsertPostReactionRequest(post_id=post.id, reaction_type=None)
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_user_reaction", AsyncMock(return_value=_reaction(ReactionType.like))),
        patch.object(svc, "delete_user_reaction", AsyncMock(return_value=_reaction())) as delete_reaction,
        patch.object(svc, "update_post_like_count", AsyncMock(return_value=2)) as update_count,
    ):
        response = await svc.upsert_post_reaction(db, user_id, payload)

    assert response.status is True
    assert response.message == "Reaction removed successfully"
    assert response.data.like_count == 2
    assert response.data.user_reaction is None
    delete_reaction.assert_awaited_once()
    update_count.assert_awaited_once_with(db, post.id, -1)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_post_reaction_remove_idempotent_when_missing(mock_db):
    post = _post(like_count=7)
    user_id = uuid.uuid4()
    payload = UpsertPostReactionRequest(post_id=post.id, reaction_type=None)
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_user_reaction", AsyncMock(return_value=None)),
        patch.object(svc, "delete_user_reaction", AsyncMock()) as delete_reaction,
        patch.object(svc, "update_post_like_count", AsyncMock()) as update_count,
    ):
        response = await svc.upsert_post_reaction(db, user_id, payload)

    assert response.status is True
    assert response.message == "Reaction removed successfully"
    assert response.data.like_count == 7
    assert response.data.user_reaction is None
    delete_reaction.assert_not_called()
    update_count.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "reaction_type",
    ["like", "celebrate", "insightful", "support", "curious", "LIKE", "CELEBRATE"],
)
def test_upsert_post_reaction_request_accepts_all_reaction_types(reaction_type):
    payload = UpsertPostReactionRequest(post_id=uuid.uuid4(), reaction_type=reaction_type)
    assert payload.reaction_type == ReactionType(reaction_type.lower())


@pytest.mark.asyncio
async def test_upsert_post_reaction_creates_celebrate_increments_like_count(mock_db):
    post = _post(like_count=4)
    user_id = uuid.uuid4()
    payload = UpsertPostReactionRequest(post_id=post.id, reaction_type="celebrate")
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_user_reaction", AsyncMock(return_value=None)),
        patch.object(svc, "upsert_user_reaction", AsyncMock()) as upsert,
        patch.object(svc, "update_post_like_count", AsyncMock(return_value=5)) as update_count,
    ):
        response = await svc.upsert_post_reaction(db, user_id, payload)

    assert response.data.like_count == 5
    assert response.data.user_reaction == "CELEBRATE"
    upsert.assert_awaited_once()
    update_count.assert_awaited_once_with(db, post.id, 1)


@pytest.mark.asyncio
async def test_upsert_post_reaction_switch_like_to_celebrate_does_not_change_like_count(mock_db):
    post = _post(like_count=5)
    user_id = uuid.uuid4()
    payload = UpsertPostReactionRequest(post_id=post.id, reaction_type="celebrate")
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_user_reaction", AsyncMock(return_value=_reaction(ReactionType.like))),
        patch.object(svc, "upsert_user_reaction", AsyncMock()) as upsert,
        patch.object(svc, "update_post_like_count", AsyncMock(return_value=5)) as update_count,
    ):
        response = await svc.upsert_post_reaction(db, user_id, payload)

    assert response.data.like_count == 5
    assert response.data.user_reaction == "CELEBRATE"
    upsert.assert_awaited_once()
    update_count.assert_awaited_once_with(db, post.id, 0)


@pytest.mark.asyncio
async def test_upsert_post_reaction_post_not_found(mock_db):
    db = mock_db()
    payload = UpsertPostReactionRequest(post_id=uuid.uuid4(), reaction_type="LIKE")

    with patch.object(svc, "get_post_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await svc.upsert_post_reaction(db, uuid.uuid4(), payload)

    assert exc.value.status_code == 404
