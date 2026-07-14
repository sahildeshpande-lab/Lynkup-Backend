from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from apps.engagement.services import share_service as svc


def _post():
    return SimpleNamespace(id=uuid.uuid4())


def _share_event():
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_share_post_creates_share_event(mock_db):
    post = _post()
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_profile_id_for_user", AsyncMock(return_value=profile_id)),
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=None)),
        patch.object(svc, "create_share_event", AsyncMock(return_value=_share_event())) as create_share,
        patch.object(svc, "update_post_share_count", AsyncMock(return_value=1)) as update_count,
    ):
        response = await svc.share_post(db, user_id, post.id)

    assert response.status is True
    assert response.message == "Post shared successfully"
    assert response.data is None
    create_share.assert_awaited_once_with(db, user_id, post.id)
    update_count.assert_awaited_once_with(db, post.id, 1)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_share_post_repeat_is_idempotent_success(mock_db):
    post = _post()
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_profile_id_for_user", AsyncMock(return_value=profile_id)),
        patch.object(svc, "get_user_share_event", AsyncMock(return_value=_share_event())),
        patch.object(svc, "create_share_event", AsyncMock()) as create_share,
        patch.object(svc, "update_post_share_count", AsyncMock()) as update_count,
    ):
        response = await svc.share_post(db, user_id, post.id)

    assert response.status is True
    assert response.message == "Post shared successfully"
    create_share.assert_not_called()
    update_count.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_share_post_not_found(mock_db):
    db = mock_db()
    post_id = uuid.uuid4()

    with patch.object(svc, "get_post_for_update", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await svc.share_post(db, uuid.uuid4(), post_id)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_share_post_profile_not_found(mock_db):
    post = _post()
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_profile_id_for_user", AsyncMock(return_value=None)),
    ):
        with pytest.raises(HTTPException) as exc:
            await svc.share_post(db, uuid.uuid4(), post.id)

    assert exc.value.status_code == 404
