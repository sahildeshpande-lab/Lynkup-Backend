from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from apps.engagement.services import repost_service as svc
from common.enums import PostState


def _post(repost_count: int = 0, state: PostState = PostState.published):
    return SimpleNamespace(id=uuid.uuid4(), repost_count=repost_count, state=state, author_user_id=uuid.uuid4())


def _repost():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_repost_post_creates_repost_and_increments_counter(mock_db):
    post = _post(repost_count=3)
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_profile_id_for_user", AsyncMock(return_value=profile_id)),
        patch.object(svc, "get_user_repost", AsyncMock(return_value=None)),
        patch.object(svc, "create_repost", AsyncMock(return_value=_repost())) as create_repost,
        patch.object(svc, "update_post_repost_count", AsyncMock(return_value=4)) as update_count,
        patch.object(svc, "increment_posts_count_for_user", AsyncMock()) as inc_posts,
    ):
        response = await svc.toggle_repost(db, user_id, post.id, is_reposted=True)

    assert response.status is True
    assert response.message == "Post reposted successfully"
    assert response.data.post_id == post.id
    assert response.data.is_reposted is True
    assert response.data.repost_count == 4
    create_repost.assert_awaited_once_with(db, profile_id, user_id, post.id)
    update_count.assert_awaited_once_with(db, post.id, 1)
    inc_posts.assert_awaited_once_with(db, user_id)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_repost_post_returns_success_when_already_reposted(mock_db):
    post = _post(repost_count=5)
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_profile_id_for_user", AsyncMock(return_value=profile_id)),
        patch.object(svc, "get_user_repost", AsyncMock(return_value=_repost())),
        patch.object(svc, "create_repost", AsyncMock()) as create_repost,
        patch.object(svc, "update_post_repost_count", AsyncMock()) as update_count,
    ):
        response = await svc.toggle_repost(db, user_id, post.id, is_reposted=True)

    assert response.status is True
    assert response.message == "Post reposted successfully"
    assert response.data.post_id == post.id
    assert response.data.is_reposted is True
    assert response.data.repost_count == 5
    create_repost.assert_not_called()
    update_count.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_remove_repost_deletes_repost_and_decrements_counter(mock_db):
    post = _post(repost_count=3)
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    existing_repost = _repost()
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_profile_id_for_user", AsyncMock(return_value=profile_id)),
        patch.object(svc, "get_user_repost", AsyncMock(return_value=existing_repost)),
        patch.object(svc, "delete_repost", AsyncMock()) as delete_repost,
        patch.object(svc, "update_post_repost_count", AsyncMock(return_value=2)) as update_count,
        patch.object(svc, "decrement_posts_count_for_user", AsyncMock()) as dec_posts,
    ):
        response = await svc.toggle_repost(db, user_id, post.id, is_reposted=False)

    assert response.status is True
    assert response.message == "Repost removed successfully"
    assert response.data.post_id == post.id
    assert response.data.is_reposted is False
    assert response.data.repost_count == 2
    delete_repost.assert_awaited_once_with(db, existing_repost)
    update_count.assert_awaited_once_with(db, post.id, -1)
    dec_posts.assert_awaited_once_with(db, user_id)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_remove_repost_returns_success_when_not_reposted(mock_db):
    post = _post(repost_count=2)
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_profile_id_for_user", AsyncMock(return_value=profile_id)),
        patch.object(svc, "get_user_repost", AsyncMock(return_value=None)),
        patch.object(svc, "delete_repost", AsyncMock()) as delete_repost,
        patch.object(svc, "update_post_repost_count", AsyncMock()) as update_count,
    ):
        response = await svc.toggle_repost(db, user_id, post.id, is_reposted=False)

    assert response.status is True
    assert response.message == "Repost removed successfully"
    assert response.data.post_id == post.id
    assert response.data.is_reposted is False
    assert response.data.repost_count == 2
    delete_repost.assert_not_called()
    update_count.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_repost_post_not_found(mock_db):
    db = mock_db()
    post_id = uuid.uuid4()

    with patch.object(svc, "get_post_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await svc.toggle_repost(db, uuid.uuid4(), post_id, is_reposted=True)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_repost_post_profile_not_found(mock_db):
    post = _post()
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_profile_id_for_user", AsyncMock(return_value=None)),
    ):
        with pytest.raises(HTTPException) as exc:
            await svc.toggle_repost(db, uuid.uuid4(), post.id, is_reposted=True)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_repost_non_published_post_fails(mock_db):
    post = _post(state=PostState.draft)
    db = mock_db()

    with patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)):
        with pytest.raises(HTTPException) as exc:
            await svc.toggle_repost(db, uuid.uuid4(), post.id, is_reposted=True)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_repost_own_post_fails(mock_db):
    author_id = uuid.uuid4()
    post = _post()
    post.author_user_id = author_id
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_profile_id_for_user", AsyncMock()) as get_profile,
        patch.object(svc, "create_repost", AsyncMock()) as create_repost,
    ):
        with pytest.raises(HTTPException) as exc:
            await svc.toggle_repost(db, author_id, post.id, is_reposted=True)

    assert exc.value.status_code == 400
    assert exc.value.detail == "You cannot repost your own post"
    get_profile.assert_not_called()
    create_repost.assert_not_called()
    db.commit.assert_not_called()
