"""Tests for the distributed Semantic Scholar rate limiter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.learningspotlight.services.semantic_scholar_rate_limiter import (
    acquire_semantic_scholar_permit,
    reset_local_rate_limiter_state,
)


@pytest.fixture(autouse=True)
def _reset_limiter():
    reset_local_rate_limiter_state()
    yield
    reset_local_rate_limiter_state()


@pytest.mark.asyncio
async def test_local_rate_limiter_spaces_requests() -> None:
    sleep = AsyncMock()
    with (
        patch(
            "apps.learningspotlight.services.semantic_scholar_rate_limiter.get_redis_client",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "apps.learningspotlight.services.semantic_scholar_rate_limiter.asyncio.sleep",
            new=sleep,
        ),
    ):
        wait1 = await acquire_semantic_scholar_permit(rps=10.0)
        wait2 = await acquire_semantic_scholar_permit(rps=10.0)

    assert wait1 == 0.0
    assert wait2 > 0.0
    sleep.assert_awaited()


@pytest.mark.asyncio
async def test_redis_rate_limiter_uses_shared_script() -> None:
    redis_client = MagicMock()
    redis_client.eval = AsyncMock(return_value=250)  # 250ms wait
    sleep = AsyncMock()

    with (
        patch(
            "apps.learningspotlight.services.semantic_scholar_rate_limiter.get_redis_client",
            new=AsyncMock(return_value=redis_client),
        ),
        patch(
            "apps.learningspotlight.services.semantic_scholar_rate_limiter.close_redis_client",
            new=AsyncMock(),
        ),
        patch(
            "apps.learningspotlight.services.semantic_scholar_rate_limiter.asyncio.sleep",
            new=sleep,
        ),
    ):
        waited = await acquire_semantic_scholar_permit(rps=1.0)

    assert waited == pytest.approx(0.25)
    redis_client.eval.assert_awaited()
    sleep.assert_awaited_once()
    assert sleep.await_args.args[0] == pytest.approx(0.25)
