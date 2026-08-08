from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.profiles.db_models import LearningRecommendationLog, Profile

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RecommendationPersistenceService:
    """Persist learning recommendation snapshots (cron output) with archival history.

    This service is intentionally responsible only for persistence and archival.
    It does NOT generate recommendations and does NOT modify the upstream
    Semantic Scholar integration/response shape.
    """

    async def save_learning_recommendations(
        self,
        session: AsyncSession,
        user_id: UUID,
        recommendation_json: dict[str, Any],
    ) -> None:
        """Persist the latest learning recommendation snapshot for one user.

        Behavior:
        - If `profiles.learning_recommendations` is NOT NULL, archive the
          COMPLETE previous JSON into `learning_recommendation_logs`.
        - Update the profile's `learning_recommendations` and
          `recommendations_updated_at`.
        - All operations run inside ONE database transaction.
        """
        previous_snapshot_found = False
        history_created = False
        profile_updated = False

        async with session.begin():
            profile = (
                await session.execute(
                    select(Profile).where(Profile.user_id == user_id)
                )
            ).scalar_one_or_none()

            if profile is None:
                raise ValueError(f"Profile not found for user_id={user_id}")

            if profile.learning_recommendations is not None:
                previous_snapshot_found = True
                await self.archive_previous_recommendation(
                    session=session,
                    user_id=user_id,
                    previous_snapshot=profile.learning_recommendations,
                )
                history_created = True

            await self.update_profile_recommendation(
                session=session,
                profile=profile,
                recommendation_json=recommendation_json,
            )
            profile_updated = True

        logger.info(
            "[recommendation-storage] user_id=%s previous_snapshot_found=%s history_created=%s profile_updated=%s",
            user_id,
            previous_snapshot_found,
            history_created,
            profile_updated,
        )

    async def archive_previous_recommendation(
        self,
        session: AsyncSession,
        user_id: UUID,
        previous_snapshot: dict[str, Any],
    ) -> None:
        """Insert the previous profile snapshot into the history table."""
        now = _utc_now()
        log_entry = LearningRecommendationLog(
            user_id=user_id,
            learning_recommendations=previous_snapshot,
            created_at=now,
            updated_at=now,
        )
        session.add(log_entry)

    async def update_profile_recommendation(
        self,
        session: AsyncSession,
        profile: Profile,
        recommendation_json: dict[str, Any],
    ) -> None:
        """Update the profile with the latest recommendation snapshot."""
        profile.learning_recommendations = recommendation_json
        profile.recommendations_updated_at = _utc_now()
        session.add(profile)

