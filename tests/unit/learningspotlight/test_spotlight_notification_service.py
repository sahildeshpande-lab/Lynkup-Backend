from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from apps.learningspotlight.services.spotlight_notification_service import (
    notify_learning_spotlight_recommended_best_effort,
)
from common.enums import SpotlightType


@pytest.mark.asyncio
async def test_notify_learning_spotlight_skips_push_when_push_disabled():
    user_id = uuid.uuid4()

    with (
        patch(
            "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService.is_push_notification_enabled",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "core.database.session.async_session_factory",
        ) as factory,
        patch(
            "apps.notifications.services.notification_service.notify_learning_spotlight_recommended",
            new=AsyncMock(),
        ) as notify,
    ):
        session = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)

        await notify_learning_spotlight_recommended_best_effort(
            user_id,
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=1,
        )

    notify.assert_awaited_once()
    assert notify.await_args.kwargs["send_push"] is False


@pytest.mark.asyncio
async def test_notify_learning_spotlight_sends_push_when_push_enabled():
    user_id = uuid.uuid4()

    with (
        patch(
            "apps.recommendations.services.recommendation_settings_service.RecommendationSettingsService.is_push_notification_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "core.database.session.async_session_factory",
        ) as factory,
        patch(
            "apps.notifications.services.notification_service.notify_learning_spotlight_recommended",
            new=AsyncMock(),
        ) as notify,
    ):
        session = AsyncMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=None)

        await notify_learning_spotlight_recommended_best_effort(
            user_id,
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=1,
        )

    notify.assert_awaited_once()
