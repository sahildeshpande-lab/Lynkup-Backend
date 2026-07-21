from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import Index, UniqueConstraint
from sqlalchemy.sql.schema import CheckConstraint

from apps.engagement.db_models import Comment, CommentReaction
from apps.engagement.schemas import CreateCommentRequest, DeleteCommentRequest
from apps.engagement.services import comment_service as svc
from common.enums import ReactionType


def _comment(
    *,
    level: int = 1,
    parent_comment_id=None,
    post_id=None,
    user_id=None,
    is_deleted: bool = False,
    comment_text: str = "Hello",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        post_id=post_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        parent_comment_id=parent_comment_id,
        level=level,
        is_deleted=is_deleted,
        like_count=0,
        reply_count=0,
        comment_text=comment_text,
        created_at=datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc),
    )


def _profile():
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        first_name="Jane",
        last_name="Doe",
        major="CS",
        minor="",
        edu_level="Bachelors",
        profile_photo_url=None,
        bio="Campus ambassador",
    )


@pytest.mark.asyncio
async def test_create_top_level_comment(mock_db):
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db = mock_db()
    payload = CreateCommentRequest(post_id=post_id, comment_text="Great post!")

    created = _comment(level=1, post_id=post_id, user_id=user_id, comment_text="Great post!")

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "create_comment", AsyncMock(return_value=created)) as create_fn,
        patch.object(svc, "increment_reply_count", AsyncMock()) as increment_reply,
        patch.object(svc, "update_post_comment_count", AsyncMock()) as update_comment_count,
        patch.object(svc, "fetch_profiles_by_user_ids", AsyncMock(return_value={})),
    ):
        response = await svc.create_post_comment(db, user_id, payload)

    create_fn.assert_awaited_once()
    kwargs = create_fn.await_args.kwargs
    assert kwargs["post_id"] == post_id
    assert kwargs["user_id"] == user_id
    assert kwargs["parent_comment_id"] is None
    assert kwargs["level"] == 1
    increment_reply.assert_not_called()
    update_comment_count.assert_awaited_once_with(db, post_id, 1)
    db.commit.assert_awaited_once()
    assert response.status is True
    assert response.message == "Comment created successfully"
    assert response.data.level == 1
    assert response.data.comment_text == "Great post!"
    assert response.data.can_delete_comment is True


@pytest.mark.asyncio
async def test_create_reply_returns_created_comment(mock_db):
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    parent = _comment(level=1, post_id=post_id)
    parent.reply_count = 2
    db = mock_db()
    payload = CreateCommentRequest(
        post_id=post_id,
        comment_text="Nice reply",
        parent_comment_id=parent.id,
    )
    created = _comment(
        level=2,
        post_id=post_id,
        user_id=user_id,
        parent_comment_id=parent.id,
        comment_text="Nice reply",
    )

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "get_comment_by_id", AsyncMock(return_value=parent)),
        patch.object(svc, "create_comment", AsyncMock(return_value=created)) as create_fn,
        patch.object(svc, "increment_reply_count", AsyncMock()),
        patch.object(svc, "fetch_profiles_by_user_ids", AsyncMock(return_value={})),
        patch.object(svc, "fetch_user_comment_reactions", AsyncMock(return_value={})),
    ):
        response = await svc.create_post_comment(db, user_id, payload)

    assert create_fn.await_args.kwargs["level"] == 2
    assert response.status is True
    assert response.message == "Reply created successfully"
    assert response.data.id == created.id
    assert response.data.parent_comment_id == parent.id
    assert response.data.comment_text == "Nice reply"
    assert response.data.level == 2
    assert response.data.replies == []


@pytest.mark.asyncio
async def test_create_reply_increments_parent_reply_count(mock_db):
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    parent = _comment(level=1, post_id=post_id)
    db = mock_db()
    payload = CreateCommentRequest(
        post_id=post_id,
        comment_text="Nice reply",
        parent_comment_id=parent.id,
    )
    created = _comment(
        level=2,
        post_id=post_id,
        user_id=user_id,
        parent_comment_id=parent.id,
        comment_text="Nice reply",
    )

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "get_comment_by_id", AsyncMock(return_value=parent)),
        patch.object(svc, "create_comment", AsyncMock(return_value=created)),
        patch.object(svc, "increment_reply_count", AsyncMock()) as increment_reply,
        patch.object(svc, "fetch_profiles_by_user_ids", AsyncMock(return_value={})),
        patch.object(svc, "fetch_user_comment_reactions", AsyncMock(return_value={})),
    ):
        response = await svc.create_post_comment(db, user_id, payload)

    increment_reply.assert_awaited_once_with(db, parent.id)
    assert response.data.level == 2
    assert response.data.replies == []


@pytest.mark.asyncio
async def test_create_reply_rejected_when_parent_level_is_max(mock_db):
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    parent = _comment(level=3, post_id=post_id)
    db = mock_db()
    payload = CreateCommentRequest(
        post_id=post_id,
        comment_text="Too deep",
        parent_comment_id=parent.id,
    )

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "get_comment_by_id", AsyncMock(return_value=parent)),
    ):
        response = await svc.create_post_comment(db, user_id, payload)

    assert response.status is False
    assert response.message == "Maximum nesting depth of 3 exceeded"
    assert response.data is None


@pytest.mark.asyncio
async def test_create_comment_post_not_found(mock_db):
    db = mock_db()
    with patch.object(svc, "post_exists", AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc:
            await svc.create_post_comment(
                db,
                uuid.uuid4(),
                CreateCommentRequest(post_id=uuid.uuid4(), comment_text="Hi"),
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_post_comments_nested_structure(mock_db):
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db = mock_db()

    top = _comment(level=1, post_id=post_id)
    reply = _comment(level=2, post_id=post_id, parent_comment_id=top.id, comment_text="Reply")
    profile = _profile()
    profile.user_id = top.user_id

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "count_top_level_comments", AsyncMock(return_value=1)),
        patch.object(svc, "fetch_top_level_comments", AsyncMock(return_value=[(top, profile, None)])),
        patch.object(svc, "fetch_comments_by_parent_ids", AsyncMock(return_value=[reply])),
        patch.object(svc, "fetch_profiles_by_user_ids", AsyncMock(return_value={top.user_id: (profile, None), reply.user_id: (profile, None)})),
        patch.object(svc, "fetch_user_comment_reactions", AsyncMock(return_value={})),
    ):
        response = await svc.get_post_comments(db, user_id, post_id)

    assert response.status is True
    assert len(response.data.comments) == 1
    assert response.data.comments[0].level == 1
    assert response.data.comments[0].can_delete_comment is False
    assert response.data.comments[0].replies[0].comment_text == "Reply"
    assert response.data.comments[0].replies[0].level == 2
    assert response.data.comments[0].replies[0].can_delete_comment is False
    assert response.data.comments[0].author.bio == "Campus ambassador"


@pytest.mark.asyncio
async def test_soft_delete_comment(mock_db):
    user_id = uuid.uuid4()
    comment = _comment(user_id=user_id)
    db = mock_db()

    async def _mark_deleted(_db, comment_obj, *, now=None):
        comment_obj.is_deleted = True
        return comment_obj

    with (
        patch.object(svc, "get_comment_for_update", AsyncMock(return_value=comment)),
        patch.object(svc, "update_post_comment_count", AsyncMock()) as update_comment_count,
        patch.object(svc, "mark_comment_deleted", AsyncMock(side_effect=_mark_deleted)),
        patch.object(svc, "fetch_profiles_by_user_ids", AsyncMock(return_value={})),
    ):
        response = await svc.delete_comment(
            db,
            user_id,
            DeleteCommentRequest(post_id=comment.post_id, comment_id=comment.id),
        )

    assert response.status is True
    assert response.data.is_deleted is True
    assert response.data.comment_text == "Hello"
    assert response.data.can_delete_comment is True
    update_comment_count.assert_awaited_once_with(db, comment.post_id, -1)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_comment_forbidden_for_non_author(mock_db):
    author_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    comment = _comment(user_id=author_id)
    db = mock_db()

    with patch.object(svc, "get_comment_for_update", AsyncMock(return_value=comment)):
        response = await svc.delete_comment(
            db,
            other_user_id,
            DeleteCommentRequest(post_id=comment.post_id, comment_id=comment.id),
        )

    assert response.status is False
    assert response.message == "Not the authenticated user"
    assert response.data is None
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_get_post_comments_can_delete_only_for_author(mock_db):
    post_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    db = mock_db()

    own_comment = _comment(level=1, post_id=post_id, user_id=viewer_id)
    other_comment = _comment(level=1, post_id=post_id)
    own_reply = _comment(
        level=2,
        post_id=post_id,
        parent_comment_id=other_comment.id,
        user_id=viewer_id,
        comment_text="My reply",
    )

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "count_top_level_comments", AsyncMock(return_value=2)),
        patch.object(
            svc,
            "fetch_top_level_comments",
            AsyncMock(return_value=[(own_comment, None, None), (other_comment, None, None)]),
        ),
        patch.object(svc, "fetch_comments_by_parent_ids", AsyncMock(return_value=[own_reply])),
        patch.object(svc, "fetch_profiles_by_user_ids", AsyncMock(return_value={})),
        patch.object(svc, "fetch_user_comment_reactions", AsyncMock(return_value={})),
    ):
        response = await svc.get_post_comments(db, viewer_id, post_id)

    assert response.data.comments[0].can_delete_comment is True
    assert response.data.comments[1].can_delete_comment is False
    assert response.data.comments[1].replies[0].can_delete_comment is True
    assert response.data.comments[1].replies[0].comment_text == "My reply"


@pytest.mark.asyncio
async def test_reply_remains_after_parent_deletion(mock_db):
    user_id = uuid.uuid4()
    post_id = uuid.uuid4()
    parent = _comment(level=1, post_id=post_id, user_id=user_id)
    reply = _comment(
        level=2,
        post_id=post_id,
        parent_comment_id=parent.id,
        comment_text="Still here",
    )
    deleted_parent = _comment(
        level=1,
        post_id=post_id,
        user_id=user_id,
        is_deleted=True,
        comment_text="Original parent text",
    )
    deleted_parent.id = parent.id
    db = mock_db()

    with (
        patch.object(svc, "get_comment_for_update", AsyncMock(return_value=parent)),
        patch.object(svc, "update_post_comment_count", AsyncMock()) as update_comment_count,
        patch.object(svc, "mark_comment_deleted", AsyncMock(return_value=deleted_parent)),
        patch.object(svc, "fetch_profiles_by_user_ids", AsyncMock(return_value={})),
    ):
        await svc.delete_comment(
            db,
            user_id,
            DeleteCommentRequest(post_id=parent.post_id, comment_id=parent.id),
        )

    update_comment_count.assert_awaited_once_with(db, parent.post_id, -1)

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "count_top_level_comments", AsyncMock(return_value=1)),
        patch.object(svc, "fetch_top_level_comments", AsyncMock(return_value=[(deleted_parent, None, None)])),
        patch.object(svc, "fetch_comments_by_parent_ids", AsyncMock(return_value=[reply])),
        patch.object(svc, "fetch_profiles_by_user_ids", AsyncMock(return_value={})),
        patch.object(svc, "fetch_user_comment_reactions", AsyncMock(return_value={})),
    ):
        response = await svc.get_post_comments(db, user_id, post_id)

    assert response.data.comments[0].comment_text == "Original parent text"
    assert response.data.comments[0].replies[0].comment_text == "Still here"
    assert response.data.comments[0].replies[0].parent_comment_id == parent.id


@pytest.mark.asyncio
async def test_get_post_comments_pagination(mock_db):
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "count_top_level_comments", AsyncMock(return_value=45)),
        patch.object(svc, "fetch_top_level_comments", AsyncMock(return_value=[])) as fetch_top,
        patch.object(svc, "fetch_user_comment_reactions", AsyncMock(return_value={})),
    ):
        response = await svc.get_post_comments(db, user_id, post_id, page=2, page_size=20)

    fetch_top.assert_awaited_once_with(db, post_id, offset=20, limit=20)
    assert response.data.totalItems == 45
    assert response.data.page == 2
    assert response.data.pageSize == 20
    assert response.data.totalPages == 3


@pytest.mark.asyncio
async def test_get_post_comments_without_pagination_returns_all(mock_db):
    post_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "post_exists", AsyncMock(return_value=True)),
        patch.object(svc, "count_top_level_comments", AsyncMock(return_value=2)),
        patch.object(svc, "fetch_top_level_comments", AsyncMock(return_value=[])) as fetch_top,
        patch.object(svc, "fetch_user_comment_reactions", AsyncMock(return_value={})),
    ):
        response = await svc.get_post_comments(db, user_id, post_id)

    fetch_top.assert_awaited_once_with(db, post_id, offset=0, limit=None)
    assert response.data.page == 1
    assert response.data.pageSize == 2
    assert response.data.totalItems == 2
    assert response.data.totalPages == 1


def test_comment_model_indexes_constraints_and_relationships():
    table_args = Comment.__table_args__
    check_constraints = [arg for arg in table_args if isinstance(arg, CheckConstraint)]
    assert len(check_constraints) == 1
    assert "level >= 1 AND level <= 3" in str(check_constraints[0].sqltext)
    index_names = {arg.name for arg in table_args if isinstance(arg, Index)}
    assert index_names == {
        "ix_comments_post_id",
        "ix_comments_parent_comment_id",
        "ix_comments_user_id",
    }
    assert hasattr(Comment, "post")
    assert hasattr(Comment, "author")
    assert hasattr(Comment, "parent_comment")
    assert hasattr(Comment, "replies")
    assert hasattr(Comment, "reactions")


def test_comment_reaction_model_indexes_constraints_and_relationships():
    table_args = CommentReaction.__table_args__
    index_names = {arg.name for arg in table_args if isinstance(arg, Index)}
    unique_names = {arg.name for arg in table_args if isinstance(arg, UniqueConstraint)}
    assert index_names == {
        "ix_comment_reactions_comment_id",
        "ix_comment_reactions_user_id",
    }
    assert "uq_comment_reactions_comment_user" in unique_names
    assert hasattr(CommentReaction, "comment")
    assert hasattr(CommentReaction, "user")
