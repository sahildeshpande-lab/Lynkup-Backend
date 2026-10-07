from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from core.lifespan import lifespan


def _patch_recommendation_init(monkeypatch) -> MagicMock:
    init_mock = MagicMock()
    monkeypatch.setattr(
        "apps.recommendations.services.algorithm.initialize_models",
        init_mock,
    )
    return init_mock


def test_lifespan_source_does_not_start_apscheduler() -> None:
    source = inspect.getsource(lifespan)
    assert "register_jobs" not in source
    assert "scheduler.start" not in source
    assert "scheduler.shutdown" not in source
    assert "apscheduler" not in source.lower()
    assert "core.scheduler" not in source


@pytest.mark.asyncio
async def test_lifespan_starts_without_scheduler(monkeypatch) -> None:
    app = FastAPI()

    monkeypatch.setattr("core.lifespan.db_settings", MagicMock(auto_init_db=False))
    monkeypatch.setattr("core.email.config.settings", MagicMock(is_sendgrid_configured=True))
    monkeypatch.setattr(
        "apps.recommendations.config.settings",
        MagicMock(load_models_on_startup=False),
    )
    init_mock = _patch_recommendation_init(monkeypatch)

    async with lifespan(app):
        assert hasattr(app.state, "recommendation_models")
        assert app.state.recommendation_models.is_ready is False

    init_mock.assert_not_called()


@pytest.mark.asyncio
async def test_lifespan_loads_recommendation_models_when_enabled(monkeypatch) -> None:
    app = FastAPI()

    monkeypatch.setattr("core.lifespan.db_settings", MagicMock(auto_init_db=False))
    monkeypatch.setattr("core.email.config.settings", MagicMock(is_sendgrid_configured=True))
    monkeypatch.setattr(
        "apps.recommendations.config.settings",
        MagicMock(load_models_on_startup=True),
    )
    init_mock = _patch_recommendation_init(monkeypatch)

    async with lifespan(app):
        assert hasattr(app.state, "recommendation_models")

    init_mock.assert_called_once()


@pytest.mark.asyncio
async def test_lifespan_runs_init_db_when_enabled(monkeypatch) -> None:
    app = FastAPI()
    init_db_mock = AsyncMock()
    migrations_mock = AsyncMock()

    monkeypatch.setattr(
        "core.lifespan.db_settings",
        MagicMock(auto_init_db=True, db_host="test-host"),
    )
    monkeypatch.setattr("core.lifespan.init_db", init_db_mock)
    monkeypatch.setattr("core.email.config.settings", MagicMock(is_sendgrid_configured=False))
    monkeypatch.setattr(
        "apps.recommendations.config.settings",
        MagicMock(load_models_on_startup=False),
    )
    monkeypatch.setattr(
        "core.lifespan.run_db_migrations_programmatically",
        migrations_mock,
    )
    init_mock = _patch_recommendation_init(monkeypatch)

    async with lifespan(app):
        pass

    init_db_mock.assert_awaited_once()
    init_mock.assert_not_called()
