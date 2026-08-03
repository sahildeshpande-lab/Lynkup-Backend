from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import User
from apps.administration.schemas import RecommendationSettingsUpdateRequest
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendation.services.recommendation_query_builder import (
    build_semantic_scholar_query,
    collect_topics,
)
from apps.recommendation.services.recommendation_settings_service import (
    RecommendationSettingsService,
    settings_to_dict,
)
from apps.recommendation.services.semantic_scholar_service import search_papers
from common.schemas import ApiResponse
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Recommendation"])


@router.get("/recommendations/papers", response_model=ApiResponse)
async def search_recommendation_papers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """
    Search Semantic Scholar papers using the authenticated user's profile keywords.

    Reads ``profiles.extracted_keywords``, builds a ranked search query, and
    returns the raw Semantic Scholar bulk search response.
    """
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == current_user.id))
    ).scalar_one_or_none()

    extracted_keywords = profile.extracted_keywords if profile else None
    stored_recommendations = (
        getattr(profile, "learning_recommendations", None) if profile else None
    )
    if isinstance(stored_recommendations, dict):
        stored_result = stored_recommendations.get("result")
        if isinstance(stored_result, dict):
            raw_papers = stored_result.get("data")
            papers_found = len(raw_papers) if isinstance(raw_papers, list) else 0
            logger.info(
                "[recommendation-api]\nuser_id=%s\nsource=profile_snapshot\npapers_found=%s",
                current_user.id,
                papers_found,
            )
            message = "Papers fetched successfully" if papers_found else "No papers found"
            return ApiResponse(message=message, data=stored_result)

    topics = collect_topics(extracted_keywords or {})
    query = build_semantic_scholar_query(extracted_keywords or {})

    if not query:
        return ApiResponse(
            status=False,
            message="No profile keywords available for recommendations",
            data={"data": [], "total": 0},
        )

    logger.info(
        "[semantic-scholar]\nuser_id=%s\ntopics=%s\nquery=\n%s",
        current_user.id,
        topics,
        query,
    )

    result, status_code = await search_papers(query)

    raw_papers = result.get("data") if isinstance(result, dict) else None
    papers_found = len(raw_papers) if isinstance(raw_papers, list) else 0

    logger.info(
        "[semantic-scholar]\nstatus_code=%s\npapers_found=%s",
        status_code,
        papers_found,
    )

    if status_code == 429:
        logger.warning(
            "[semantic-scholar] rate limit exceeded user_id=%s query=%r",
            current_user.id,
            query,
        )
        return ApiResponse(
            status=False,
            message="Semantic Scholar rate limit exceeded. Please try again in a few seconds.",
            data={"data": [], "total": 0},
        )

    message = "Papers fetched successfully" if papers_found else "No papers found"
    return ApiResponse(message=message, data=result)


@router.get("/admin/recommendation-settings", response_model=ApiResponse)
async def get_recommendation_settings(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    """Return the global recommendation cron configuration (creates defaults if missing)."""
    logger.info(
        "[recommendation-settings]\nadmin_id=%s\naction=GET",
        current_user.id,
    )

    settings = await RecommendationSettingsService().get_settings(
        db,
        admin_user_id=current_user.id,
        create_if_missing=True,
    )
    return ApiResponse(
        message="Recommendation settings fetched successfully.",
        data=settings_to_dict(settings),
    )


@router.patch("/admin/recommendation-settings", response_model=ApiResponse)
async def patch_recommendation_settings(
    payload: RecommendationSettingsUpdateRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    """Update recommendation cron configuration. All body fields are optional."""
    changes = payload.model_dump(exclude_unset=True)
    logger.info(
        "[recommendation-settings]\nadmin_id=%s\naction=PATCH\nchanges=%s",
        current_user.id,
        changes,
    )

    try:
        updated = await RecommendationSettingsService().update_settings(
            db,
            admin_user_id=current_user.id,
            is_enabled=payload.is_enabled,
            generation_frequency_days=payload.generation_frequency_days,
            max_recommendations=payload.max_recommendations,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return ApiResponse(
        message="Recommendation settings updated successfully.",
        data=settings_to_dict(updated),
    )
