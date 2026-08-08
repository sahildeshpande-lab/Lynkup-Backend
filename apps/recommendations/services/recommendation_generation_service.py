from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendations.services.recommendation_persistence_service import (
    RecommendationPersistenceService,
)
from apps.recommendations.services.recommendation_query_builder import (
    build_semantic_scholar_query,
)
from apps.recommendations.services.semantic_scholar_service import search_papers

logger = logging.getLogger(__name__)


class RecommendationGenerationService:
    """Own the single recommendation generation pipeline (query → SS → persist).

    Cron decides *when* to generate; this service decides *how*.
    """

    def __init__(
        self,
        *,
        persistence_service: RecommendationPersistenceService | None = None,
    ) -> None:
        self._persistence_service = (
            persistence_service or RecommendationPersistenceService()
        )

    async def generate_for_user(
        self,
        session: AsyncSession,
        user_id: UUID,
        max_recommendations: int,
    ) -> None:
        """Generate and persist learning recommendations for one user.

        Steps:
        1. Read ``profiles.extracted_keywords``
        2. Build Semantic Scholar Boolean query
        3. Call Semantic Scholar
        4. Build recommendation JSON
        5. Persist via ``RecommendationPersistenceService``
        """
        logger.info(
            "[recommendation-generation]\nuser_id=%s\naction=Generation started",
            user_id,
        )

        profile = (
            await session.execute(select(Profile).where(Profile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is None:
            raise ValueError(f"Profile not found for user_id={user_id}")

        extracted_keywords = dict(profile.extracted_keywords or {})
        # Clear the read transaction so persistence can open its own begin().
        await session.rollback()

        query = build_semantic_scholar_query(extracted_keywords)
        if not query:
            raise ValueError(
                f"No searchable query from extracted_keywords for user_id={user_id}"
            )

        logger.info(
            "[recommendation-generation]\nuser_id=%s\naction=Generated query\nquery=%s",
            user_id,
            query,
        )
        logger.info(
            "[recommendation-generation]\nuser_id=%s\naction=Semantic Scholar request\n"
            "limit=%s",
            user_id,
            max_recommendations,
        )

        result, status_code = await search_papers(query, limit=max_recommendations)
        raw_papers = result.get("data") if isinstance(result, dict) else None
        if isinstance(raw_papers, list) and len(raw_papers) > max_recommendations:
            # Defensive cap: bulk SS can ignore limit; never persist more than configured.
            result = {**result, "data": raw_papers[:max_recommendations]}
            raw_papers = result["data"]
        papers_found = len(raw_papers) if isinstance(raw_papers, list) else 0

        logger.info(
            "[recommendation-generation]\nuser_id=%s\naction=Semantic Scholar response\n"
            "status_code=%s\npapers_found=%s",
            user_id,
            status_code,
            papers_found,
        )

        if status_code == 429:
            raise RuntimeError(
                f"Semantic Scholar rate limit exceeded for user_id={user_id}"
            )

        recommendation_json: dict[str, Any] = {
            "query": query,
            "status_code": status_code,
            "result": result,
        }

        logger.info(
            "[recommendation-generation]\nuser_id=%s\naction=Persistence started",
            user_id,
        )
        await self._persistence_service.save_learning_recommendations(
            session=session,
            user_id=user_id,
            recommendation_json=recommendation_json,
        )
        logger.info(
            "[recommendation-generation]\nuser_id=%s\naction=Persistence completed",
            user_id,
        )
        logger.info(
            "[recommendation-generation]\nuser_id=%s\naction=Generation completed",
            user_id,
        )
