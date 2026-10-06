from __future__ import annotations

import logging
from uuid import UUID

from common.enums import SpotlightType

logger = logging.getLogger(__name__)


async def notify_learning_spotlight_recommended_best_effort(
    user_id: UUID,
    *,
    spotlight_type: SpotlightType | str,
    cycle_day: int,
) -> None:
    """Notify a user that a Learning Spotlight was recommended. Never raises."""
    try:
        from core.database.session import async_session_factory
        from apps.notifications.services.notification_service import (
            notify_learning_spotlight_recommended,
        )
        from apps.recommendations.services.recommendation_settings_service import (
            RecommendationSettingsService,
        )

        async with async_session_factory() as session:
            settings_service = RecommendationSettingsService()
            send_push = await settings_service.is_push_notification_enabled(session)
            if not send_push:
                logger.info(
                    "Learning spotlight admin push disabled; in-app only user_id=%s",
                    user_id,
                )

            await notify_learning_spotlight_recommended(
                session,
                user_id=user_id,
                spotlight_type=spotlight_type,
                cycle_day=cycle_day,
                send_push=send_push,
            )
    except Exception:
        logger.exception(
            "Failed learning spotlight recommendation notification user_id=%s cycle_day=%s",
            user_id,
            cycle_day,
        )
