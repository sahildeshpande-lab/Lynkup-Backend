from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.administration.db_models import AdminConfiguration
from apps.administration.schemas import (
    FeatureFlagCreateRequest,
    FeatureFlagUpdateRequest,
)
from apps.administration.services import feature_flag_service as service
from common.enums import AdminConfigurationType
from common.exceptions import ApiError


def _flag(**overrides) -> AdminConfiguration:
    now = datetime.now(timezone.utc)
    data = {
        "id": uuid4(),
        "key": "chat",
        "name": "Chat",
        "description": "In-app messaging",
        "configuration_type": AdminConfigurationType.FEATURE_FLAG,
        "value": None,
        "is_enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return AdminConfiguration(**data)


def _session_with_execute(*results) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock(side_effect=list(results))
    return session


def _scalars_result(rows: list):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    return result


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = [value] if value is not None else []
    return result


@pytest.mark.asyncio
async def test_ensure_default_feature_flags_no_op_when_empty() -> None:
    session = _session_with_execute(_scalars_result([]))
    await service.ensure_default_feature_flags(session)
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_feature_flags_returns_all_items(monkeypatch) -> None:
    chat = _flag(key="chat")
    recs = _flag(
        key="recommendations",
        name="Recommendations",
        description="recs",
        is_enabled=False,
    )
    session = _session_with_execute(_scalars_result([chat, recs]))

    async def _noop(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_feature_flags", _noop)
    data = await service.list_feature_flags(session)
    assert len(data["items"]) == 2
    assert {item["key"] for item in data["items"]} == {"chat", "recommendations"}
    assert data["items"][1]["is_enabled"] is False
    assert "value" not in data["items"][0]
    assert "configuration_type" not in data["items"][0]


@pytest.mark.asyncio
async def test_list_feature_flags_query_filters_feature_flag_type(monkeypatch) -> None:
    """Ensure list only queries configuration_type=feature_flag (no thresholds)."""
    session = _session_with_execute(_scalars_result([]))

    async def _noop(_db):
        return None

    monkeypatch.setattr(service, "ensure_default_feature_flags", _noop)
    await service.list_feature_flags(session)
    stmt = session.execute.await_args.args[0]
    where_criterion = stmt._where_criteria
    assert where_criterion
    assert any("configuration_type" in str(clause) for clause in where_criterion)

@pytest.mark.asyncio
async def test_create_feature_flag_accepts_aliases() -> None:
    session = _session_with_execute(_scalar_result(None))

    async def _refresh(obj):
        obj.id = uuid4()
        obj.created_at = datetime.now(timezone.utc)
        obj.updated_at = datetime.now(timezone.utc)

    session.refresh.side_effect = _refresh
    payload = FeatureFlagCreateRequest(
        feature_key="stories",
        name="Stories",
        description="Short-lived stories",
        enabled=True,
    )
    created = await service.create_feature_flag(payload, session)
    assert created["key"] == "stories"
    assert created["is_enabled"] is True
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_feature_flag_duplicate() -> None:
    session = _session_with_execute(_scalar_result(_flag(key="stories")))
    payload = FeatureFlagCreateRequest(key="stories", name="Stories")
    with pytest.raises(ApiError, match="already exists"):
        await service.create_feature_flag(payload, session)


@pytest.mark.asyncio
async def test_update_feature_flag_accepts_aliases() -> None:
    row = _flag(is_enabled=True)
    session = _session_with_execute(_scalar_result(row))
    payload = FeatureFlagUpdateRequest(feature_key="chat", enabled=False)
    updated = await service.update_feature_flag(payload, session)
    assert updated["is_enabled"] is False
    assert row.is_enabled is False


@pytest.mark.asyncio
async def test_update_feature_flag_missing() -> None:
    session = _session_with_execute(_scalar_result(None))
    payload = FeatureFlagUpdateRequest(key="missing", is_enabled=True)
    with pytest.raises(ApiError, match="not found"):
        await service.update_feature_flag(payload, session)


@pytest.mark.asyncio
async def test_delete_feature_flag_by_id() -> None:
    flag_id = uuid4()
    row = _flag(id=flag_id, key="stories", name="Stories")
    session = _session_with_execute(_scalar_result(row))
    session.delete = AsyncMock()

    deleted = await service.delete_feature_flag(flag_id, session)

    assert deleted["id"] == str(flag_id)
    assert deleted["key"] == "stories"
    session.delete.assert_awaited_once_with(row)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_feature_flag_missing() -> None:
    session = _session_with_execute(_scalar_result(None))
    session.delete = AsyncMock()
    with pytest.raises(ApiError, match="not found"):
        await service.delete_feature_flag(uuid4(), session)
    session.delete.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_is_feature_enabled() -> None:
    session = _session_with_execute(_scalar_result(False))
    assert await service.is_feature_enabled(session, "chat") is False

    session = _session_with_execute(_scalar_result(None))
    assert await service.is_feature_enabled(session, "unknown", default=True) is True


def test_schema_alias_normalization() -> None:
    create = FeatureFlagCreateRequest(
        feature_key=" Stories ",
        name=" Stories ",
        enabled=False,
    )
    assert create.key == "stories"
    assert create.name == "Stories"
    assert create.is_enabled is False

    update = FeatureFlagUpdateRequest(key="CHAT", is_enabled=True)
    assert update.key == "chat"
