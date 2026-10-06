from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.threshold_configuration.schemas import (
    ApiResponse,
    UpdateModerationThresholdsRequest,
)
from apps.threshold_configuration.services import (
    get_moderation_thresholds,
    update_moderation_thresholds,
)
from apps.administration.dependencies import require_signed_moderator_or_viewer
from core.database.session import get_session

router = APIRouter(tags=["Threshold Configuration"])


@router.get("/admin/threshold", response_model=ApiResponse)
async def get_admin_thresholds(
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    _ = current_user
    data = await get_moderation_thresholds(db)
    return ApiResponse(
        message="Moderation thresholds retrieved successfully",
        data=data.model_dump(),
    )


@router.patch("/admin/threshold", response_model=ApiResponse)
async def patch_admin_thresholds(
    payload: UpdateModerationThresholdsRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    data = await update_moderation_thresholds(
        payload,
        db,
        actor_user_id=current_user.id,
        actor_role=current_user.role,
    )
    return ApiResponse(
        message="Moderation thresholds updated successfully",
        data=data.model_dump(),
    )
