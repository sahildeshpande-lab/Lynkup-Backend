from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_email_cron(monkeypatch) -> None:
    app = FastAPI()

    cron_started = asyncio.Event()
    cron_cancelled = asyncio.Event()

    async def _mock_cron_send_emails():
        cron_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cron_cancelled.set()
            raise

    monkeypatch.setattr("core.lifespan.db_settings", MagicMock(auto_init_db=False))
    monkeypatch.setattr("core.email.config.settings", MagicMock(is_sendgrid_configured=True))
    monkeypatch.setattr("core.email_service.cron_send_emails", _mock_cron_send_emails)
    init_mock = _patch_recommendation_init(monkeypatch)

    async with lifespan(app):
        await asyncio.wait_for(cron_started.wait(), timeout=1)
        assert hasattr(app.state, "recommendation_models")
        assert app.state.recommendation_models.is_ready is False

    assert cron_cancelled.is_set()
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
        "core.lifespan.run_db_migrations_programmatically",
        migrations_mock,
    )
    init_mock = _patch_recommendation_init(monkeypatch)

    async def _noop_cron():
        await asyncio.sleep(0)

    monkeypatch.setattr("core.email_service.cron_send_emails", _noop_cron)

    async with lifespan(app):
        pass

    init_db_mock.assert_awaited_once()
    init_mock.assert_called_once()
