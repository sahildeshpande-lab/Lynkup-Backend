from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import User
from apps.administration.schemas import RecommendationSettingsUpdateRequest
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendations.services.recommendation_cron_service import (
    RecommendationCronService,
)
from apps.recommendations.services.recommendation_settings_service import (
    RecommendationSettingsService,
    settings_to_dict,
)
from common.pagination import paginate_items
from common.schemas import ApiResponse
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Recommendation"])

_NOT_READY_MESSAGE = (
    "Recommendations are not available yet. They will be generated during "
    "the next recommendation cycle."
)


@router.get("/recommendations/papers", response_model=ApiResponse)
async def search_recommendation_papers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_session),
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
) -> ApiResponse:
    """
    Return the authenticated user's stored learning recommendations.

    Reads ``profiles.learning_recommendations`` only. Does not call Semantic
    Scholar or generate recommendations.

    Optional ``page`` / ``pageSize`` paginate the paper list via
    ``common.pagination.paginate_items``. If both are omitted, returns all papers.
    """
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == current_user.id))
    ).scalar_one_or_none()

    stored_recommendations = (
        getattr(profile, "learning_recommendations", None) if profile else None
    )
    if isinstance(stored_recommendations, dict):
        stored_result = stored_recommendations.get("result")
        if isinstance(stored_result, dict):
            raw_papers = stored_result.get("data")
            papers = raw_papers if isinstance(raw_papers, list) else []
            papers_found = len(papers)
            logger.info(
                "[recommendation-api]\nuser_id=%s\naction=Returning stored recommendations\n"
                "papers_found=%s\npage=%s\npageSize=%s",
                current_user.id,
                papers_found,
                page,
                pageSize,
            )
            message = "Papers fetched successfully" if papers_found else "No papers found"

            if page is None and pageSize is None:
                return ApiResponse(message=message, data=stored_result)

            paginated = paginate_items(
                papers,
                page=page or 1,
                page_size=pageSize or 20,
            )
            return ApiResponse(message=message, data=paginated)

    logger.info(
        "[recommendation-api]\nuser_id=%s\naction=Recommendations not generated yet",
        current_user.id,
    )
    return ApiResponse(
        message=_NOT_READY_MESSAGE,
        data={"generated": False, "recommendations": []},
    )


@router.get("/admin/recommendation-settings", response_model=ApiResponse)
async def get_recommendation_settings(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
    page: int | None = Query(default=None, ge=1, description="Page number for settings history"),
    pageSize: int | None = Query(
        default=None,
        ge=1,
        le=200,
        description="Page size for settings history",
    ),
) -> ApiResponse:
    """Return current recommendation settings plus settings change history.

    Optional ``page`` / ``pageSize`` paginate ``history`` via
    ``common.pagination.build_paginated_response``. If both are omitted,
    ``history`` is the full list.
    """
    logger.info(
        "[recommendation-settings]\nadmin_id=%s\naction=GET\npage=%s\npageSize=%s",
        current_user.id,
        page,
        pageSize,
    )

    data = await RecommendationSettingsService().get_settings_with_history(
        db,
        admin_user_id=current_user.id,
        page=page,
        page_size=pageSize,
    )
    return ApiResponse(
        message="Recommendation settings fetched successfully.",
        data=data,
    )


@router.patch("/admin/recommendation-settings", response_model=ApiResponse)
async def patch_recommendation_settings(
    payload: RecommendationSettingsUpdateRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    """Update recommendation cron configuration only. All body fields are optional."""
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

    logger.info(
        "[recommendation-settings]\nSettings updated\nupdated_by=%s\nupdated_at=%s",
        updated.updated_by,
        updated.updated_at,
    )
    return ApiResponse(
        message="Recommendation settings updated successfully.",
        data=settings_to_dict(updated),
    )


@router.post("/admin/runcron", response_model=ApiResponse)
async def run_recommendation_cron(
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    """Manually execute recommendation generation for development/testing."""
    admin_id = current_user.id
    logger.info(
        "[recommendation-cron]\nManual execution started\nadmin_id=%s",
        admin_id,
    )

    started = time.perf_counter()
    try:
        ran = await RecommendationCronService.run_recommendation_generation()
    except Exception:
        execution_time_seconds = round(time.perf_counter() - started, 3)
        logger.exception(
            "[recommendation-cron]\nManual execution failed\nadmin_id=%s\n"
            "execution_time_seconds=%s",
            admin_id,
            execution_time_seconds,
        )
        return ApiResponse(
            status=False,
            message="Recommendation generation failed.",
        )

    execution_time_seconds = round(time.perf_counter() - started, 3)
    if not ran:
        logger.info(
            "[recommendation-cron]\nManual execution skipped\nadmin_id=%s\n"
            "execution_time_seconds=%s",
            admin_id,
            execution_time_seconds,
        )
        return ApiResponse(
            status=False,
            message=(
                "Recommendation generation is disabled or not configured. "
                "Enable it in admin recommendation settings."
            ),
        )

    logger.info(
        "[recommendation-cron]\nManual execution completed\nadmin_id=%s\n"
        "execution_time_seconds=%s",
        admin_id,
        execution_time_seconds,
    )
    return ApiResponse(
        message="Recommendation generation completed successfully.",
    )
