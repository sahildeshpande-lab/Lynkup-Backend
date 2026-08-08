from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.moderation.schemas import ApiResponse, UpdateModerationWordsRequest
from apps.moderation.services import (
    get_moderation_words,
    list_moderation_history_service,
    update_moderation_words,
)
from core.database.session import get_session
from core.security.auth import get_current_user_moderator_or_superadmin

router = APIRouter(tags=["Moderation"])


@router.get("/moderation-words", response_model=ApiResponse)
async def get_moderation_words_route(
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await get_moderation_words(db)
    return ApiResponse(message="Words fetched successfully", data=data)


@router.post("/moderation-words", response_model=ApiResponse)
async def update_moderation_words_route(
    payload: UpdateModerationWordsRequest,
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await update_moderation_words(payload, db)
    return ApiResponse(message="Words updated successfully", data=data)


@router.get("/status-history", response_model=ApiResponse)
async def get_moderation_history(
    entity_id: UUID = Query(..., description="Post or user id to fetch moderation history for"),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:
    """Return moderation history for a post or user entity."""
    _ = current_user
    data = await list_moderation_history_service(db, entity_id)
    return ApiResponse(message="Moderation history fetched successfully", data=data)
