from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from core.database.session import get_session
from core.security.auth import get_current_admin
from apps.moderation.schemas import ApiResponse, UpdateModerationWordsRequest
from apps.moderation.services import get_moderation_words, update_moderation_words

router = APIRouter(prefix="/admin", tags=["Admin Moderation"])


@router.get("/moderation-words", response_model=ApiResponse)
async def get_moderation_words_route(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    _ = current_user
    data = await get_moderation_words(db)
    return ApiResponse(message="Words fetched successfully", data=data)


@router.post("/moderation-words", response_model=ApiResponse)
async def update_moderation_words_route(
    payload: UpdateModerationWordsRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    _ = current_user
    data = await update_moderation_words(payload, db)
    return ApiResponse(message="Words updated successfully", data=data)
