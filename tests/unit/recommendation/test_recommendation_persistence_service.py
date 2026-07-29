from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from apps.recommendation.services.recommendation_persistence_service import (
    RecommendationPersistenceService,
)


class _FakeResult:
    def __init__(self, profile: Any) -> None:
        self._profile = profile

    def scalar_one_or_none(self) -> Any:
        return self._profile


class _FakeSession:
    def __init__(self, profile: Any) -> None:
        self._profile = profile
        self.execute = AsyncMock(return_value=_FakeResult(profile))
        self.add = MagicMock()

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield


@pytest.mark.asyncio
async def test_first_cron_run_updates_profile_without_creating_history(caplog) -> None:
    user_id = uuid4()
    profile = SimpleNamespace(
        user_id=user_id,
        learning_recommendations=None,
        recommendations_updated_at=None,
    )

    session = _FakeSession(profile)
    service = RecommendationPersistenceService()
    recommendation_json = {"generated_query": "x", "papers": []}

    caplog.set_level(logging.INFO)
    await service.save_learning_recommendations(
        session=session,
        user_id=user_id,
        recommendation_json=recommendation_json,
    )

    # Only profile update should be added.
    assert session.add.call_count == 1
    assert session.add.call_args[0][0] is profile

    assert profile.learning_recommendations == recommendation_json
    assert isinstance(profile.recommendations_updated_at, datetime)

    assert "[recommendation-storage]" in caplog.text
    assert "previous_snapshot_found=False" in caplog.text
    assert "history_created=False" in caplog.text
    assert "profile_updated=True" in caplog.text


@pytest.mark.asyncio
async def test_second_cron_run_archives_previous_snapshot_and_updates_profile(caplog) -> None:
    user_id = uuid4()
    previous = {"generated_query": "old", "papers": [{"id": 1}]}
    profile = SimpleNamespace(
        user_id=user_id,
        learning_recommendations=previous,
        recommendations_updated_at=None,
    )

    session = _FakeSession(profile)
    service = RecommendationPersistenceService()
    recommendation_json = {"generated_query": "new", "papers": []}

    caplog.set_level(logging.INFO)
    await service.save_learning_recommendations(
        session=session,
        user_id=user_id,
        recommendation_json=recommendation_json,
    )

    # history row + profile update
    assert session.add.call_count == 2

    added_objs = [call.args[0] for call in session.add.call_args_list]
    assert any(getattr(obj, "learning_recommendations", None) == previous for obj in added_objs)
    assert profile in added_objs

    assert profile.learning_recommendations == recommendation_json
    assert isinstance(profile.recommendations_updated_at, datetime)

    assert "[recommendation-storage]" in caplog.text
    assert "previous_snapshot_found=True" in caplog.text
    assert "history_created=True" in caplog.text
    assert "profile_updated=True" in caplog.text

