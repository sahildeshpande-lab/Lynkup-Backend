from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.administration.db_models import AdminConfiguration
from apps.threshold_configuration.schemas import (
    ModerationThresholdsData,
    UpdateModerationThresholdsRequest,
)
from apps.threshold_configuration.services import threshold_service as service
from common.enums import AdminConfigurationType
from common.exceptions import ApiError


def _threshold_row(field: str, threshold: int, **overrides) -> AdminConfiguration:
    keys = {
        "post": "moderation_post_report_threshold",
        "comment": "moderation_comment_report_threshold",
        "user": "moderation_user_report_threshold",
    }
    names = {
        "post": "Post report threshold",
        "comment": "Comment report threshold",
        "user": "User report threshold",
    }
    now = datetime.now(timezone.utc)
    data = {
        "id": uuid4(),
        "key": keys[field],
        "name": names[field],
        "description": None,
        "configuration_type": AdminConfigurationType.THRESHOLD,
        "value": {"threshold": threshold},
        "is_enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return AdminConfiguration(**data)


@pytest.mark.asyncio
async def test_get_moderation_thresholds(monkeypatch) -> None:
    rows = [
        _threshold_row("post", 10),
        _threshold_row("comment", 5),
        _threshold_row("user", 10),
    ]
    session = AsyncMock()

    async def _noop(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_thresholds", _noop)
    monkeypatch.setattr(
        service,
        "list_threshold_rows",
        AsyncMock(return_value=rows),
    )
    data = await service.get_moderation_thresholds(session)
    assert data.model_dump() == {"post": 10, "comment": 5, "user": 10}


@pytest.mark.asyncio
async def test_update_moderation_thresholds_partial(monkeypatch) -> None:
    post = _threshold_row("post", 10)
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    async def _noop(_db):
        return None

    async def _get_row(_db, key: str):
        assert key == "moderation_post_report_threshold"
        return post

    monkeypatch.setattr(service, "ensure_default_thresholds", _noop)
    monkeypatch.setattr(service, "get_threshold_row_by_key", _get_row)
    monkeypatch.setattr(
        service,
        "get_moderation_thresholds",
        AsyncMock(
            return_value=ModerationThresholdsData(post=15, comment=5, user=10)
        ),
    )

    payload = UpdateModerationThresholdsRequest(post=15)
    data = await service.update_moderation_thresholds(payload, session)
    assert post.value == {"threshold": 15}
    assert data.post == 15
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_update_missing_row(monkeypatch) -> None:
    session = AsyncMock()
    session.commit = AsyncMock()

    async def _noop(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_thresholds", _noop)
    monkeypatch.setattr(
        service,
        "get_threshold_row_by_key",
        AsyncMock(return_value=None),
    )
    payload = UpdateModerationThresholdsRequest(comment=7)
    with pytest.raises(ApiError, match="not found"):
        await service.update_moderation_thresholds(payload, session)


def test_update_request_rejects_zero_and_negative() -> None:
    with pytest.raises(ValidationError):
        UpdateModerationThresholdsRequest(post=0)
    with pytest.raises(ValidationError):
        UpdateModerationThresholdsRequest(comment=-1)
    with pytest.raises(ValidationError):
        UpdateModerationThresholdsRequest()


def test_update_request_rejects_non_integer() -> None:
    with pytest.raises(ValidationError):
        UpdateModerationThresholdsRequest(user="ten")  # type: ignore[arg-type]
