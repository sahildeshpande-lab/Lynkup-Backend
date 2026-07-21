from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from apps.engagement.schemas import UpsertCommentReactionRequest
from apps.engagement.services import comment_reaction_service as svc
from common.enums import ReactionType


def _comment(like_count: int = 0, is_deleted: bool = False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        like_count=like_count,
        is_deleted=is_deleted,
    )


def _reaction(reaction_type: ReactionType = ReactionType.like):
    return SimpleNamespace(reaction_type=reaction_type)


@pytest.mark.asyncio
async def test_add_comment_reaction(mock_db):
    comment = _comment(like_count=2)
    user_id = uuid.uuid4()
    payload = UpsertCommentReactionRequest(comment_id=comment.id, reaction_type="LIKE")
    db = mock_db()

    with (
        patch.object(svc, "get_comment_by_id", AsyncMock(return_value=comment)),
        patch.object(svc, "get_user_comment_reaction", AsyncMock(return_value=None)),
        patch.object(svc, "upsert_user_comment_reaction", AsyncMock()) as upsert,
        patch.object(svc, "update_comment_like_count", AsyncMock(return_value=3)) as update_count,
    ):
        response = await svc.upsert_comment_reaction(db, user_id, payload)

    upsert.assert_awaited_once()
    update_count.assert_awaited_once_with(db, comment.id, 1)
    db.commit.assert_awaited_once()
    assert response.status is True
    assert response.data.like_count == 3
    assert response.data.user_reaction == "LIKE"


@pytest.mark.asyncio
async def test_update_comment_reaction(mock_db):
    comment = _comment(like_count=1)
    user_id = uuid.uuid4()
    payload = UpsertCommentReactionRequest(comment_id=comment.id, reaction_type="CELEBRATE")
    db = mock_db()

    with (
        patch.object(svc, "get_comment_by_id", AsyncMock(return_value=comment)),
        patch.object(svc, "get_user_comment_reaction", AsyncMock(return_value=_reaction(ReactionType.like))),
        patch.object(svc, "upsert_user_comment_reaction", AsyncMock()) as upsert,
        patch.object(svc, "update_comment_like_count", AsyncMock(return_value=1)) as update_count,
    ):
        response = await svc.upsert_comment_reaction(db, user_id, payload)

    upsert.assert_awaited_once()
    update_count.assert_awaited_once_with(db, comment.id, 0)
    assert response.data.user_reaction == "CELEBRATE"


@pytest.mark.asyncio
async def test_remove_comment_reaction(mock_db):
    comment = _comment(like_count=4)
    user_id = uuid.uuid4()
    payload = UpsertCommentReactionRequest(comment_id=comment.id, reaction_type=None)
    db = mock_db()

    with (
        patch.object(svc, "get_comment_by_id", AsyncMock(return_value=comment)),
        patch.object(svc, "get_user_comment_reaction", AsyncMock(return_value=_reaction(ReactionType.like))),
        patch.object(svc, "delete_user_comment_reaction", AsyncMock()) as delete_reaction,
        patch.object(svc, "update_comment_like_count", AsyncMock(return_value=3)) as update_count,
    ):
        response = await svc.upsert_comment_reaction(db, user_id, payload)

    delete_reaction.assert_awaited_once()
    update_count.assert_awaited_once_with(db, comment.id, -1)
    assert response.message == "Reaction removed successfully"
    assert response.data.user_reaction is None


@pytest.mark.asyncio
async def test_comment_reaction_unique_constraint_race(mock_db):
    comment = _comment(like_count=1)
    user_id = uuid.uuid4()
    payload = UpsertCommentReactionRequest(comment_id=comment.id, reaction_type="LIKE")
    db = mock_db()

    with (
        patch.object(svc, "get_comment_by_id", AsyncMock(return_value=comment)),
        patch.object(svc, "get_user_comment_reaction", AsyncMock(side_effect=[None, _reaction(ReactionType.like)])),
        patch.object(svc, "upsert_user_comment_reaction", AsyncMock(side_effect=IntegrityError("insert", {}, Exception()))),
        patch.object(svc, "update_comment_like_count", AsyncMock()),
    ):
        response = await svc.upsert_comment_reaction(db, user_id, payload)

    db.rollback.assert_awaited_once()
    assert response.status is True
    assert response.data.user_reaction == "LIKE"


@pytest.mark.asyncio
async def test_cannot_react_to_deleted_comment(mock_db):
    comment = _comment(is_deleted=True)
    user_id = uuid.uuid4()
    payload = UpsertCommentReactionRequest(comment_id=comment.id, reaction_type="LIKE")
    db = mock_db()

    with patch.object(svc, "get_comment_by_id", AsyncMock(return_value=comment)):
        with pytest.raises(HTTPException) as exc:
            await svc.upsert_comment_reaction(db, user_id, payload)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_comment_reaction_comment_not_found(mock_db):
    user_id = uuid.uuid4()
    payload = UpsertCommentReactionRequest(comment_id=uuid.uuid4(), reaction_type="LIKE")
    db = mock_db()

    with patch.object(svc, "get_comment_by_id", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await svc.upsert_comment_reaction(db, user_id, payload)

    assert exc.value.status_code == 404
