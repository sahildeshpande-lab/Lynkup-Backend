from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from apps.engagement.services import repost_service as svc


def _post(repost_count: int = 0):
    return SimpleNamespace(id=uuid.uuid4(), repost_count=repost_count)


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
    ):
        response = await svc.repost_post(db, user_id, post.id)

    assert response.status is True
    assert response.message == "Post reposted successfully"
    assert response.data.post_id == post.id
    assert response.data.is_reposted is True
    assert response.data.repost_count == 4
    create_repost.assert_awaited_once_with(db, profile_id, post.id)
    update_count.assert_awaited_once_with(db, post.id, 1)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_repost_post_returns_false_when_already_reposted(mock_db):
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
        response = await svc.repost_post(db, user_id, post.id)

    assert response.status is False
    assert response.message == "You have already reposted this post"
    assert response.data is None
    create_repost.assert_not_called()
    update_count.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_repost_post_not_found(mock_db):
    db = mock_db()
    post_id = uuid.uuid4()

    with patch.object(svc, "get_post_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await svc.repost_post(db, uuid.uuid4(), post_id)

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
            await svc.repost_post(db, uuid.uuid4(), post.id)

    assert exc.value.status_code == 404
