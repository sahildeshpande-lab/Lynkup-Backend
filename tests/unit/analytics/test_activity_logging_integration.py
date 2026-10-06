from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from common.enums import UserActivityLogType
from apps.engagement.schemas import UpsertPostReactionRequest
from apps.engagement.services import reaction_service as svc


@pytest.mark.asyncio
async def test_like_post_records_activity_before_commit(mock_db):
    post = SimpleNamespace(id=uuid.uuid4(), like_count=0, author_user_id=uuid.uuid4())
    user_id = uuid.uuid4()
    payload = UpsertPostReactionRequest(post_id=post.id, reaction_type="LIKE")
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=post)),
        patch.object(svc, "get_user_reaction", AsyncMock(return_value=None)),
        patch.object(svc, "upsert_user_reaction", AsyncMock()),
        patch.object(svc, "update_post_like_count", AsyncMock(return_value=1)),
        patch(
            "common.user_visibility.check_post_engagement_allowed",
            AsyncMock(),
        ),
        patch(
            "apps.recommendations.services.engagement_keyword_service.apply_engagement_keyword_update_best_effort",
            AsyncMock(),
        ),
        patch(
            "apps.analytics.services.add_user_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        response = await svc.upsert_post_reaction(db, user_id, payload)

    assert response.status is True
    activity.assert_awaited_once()
    assert activity.await_args.args[1] == user_id
    assert activity.await_args.args[2] == UserActivityLogType.LIKE_POST
    assert activity.await_args.kwargs.get("commit") is False
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_like_post_does_not_record_activity_when_post_missing(mock_db):
    user_id = uuid.uuid4()
    payload = UpsertPostReactionRequest(post_id=uuid.uuid4(), reaction_type="LIKE")
    db = mock_db()

    with (
        patch.object(svc, "get_post_for_update", AsyncMock(return_value=None)),
        patch(
            "apps.analytics.services.add_user_activity_log",
            AsyncMock(),
        ) as activity,
    ):
        with pytest.raises(HTTPException):
            await svc.upsert_post_reaction(db, user_id, payload)

    activity.assert_not_awaited()
    db.commit.assert_not_awaited()
