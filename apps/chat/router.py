from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.chat.dependencies import get_current_chat_user
from apps.chat.service import StreamChatError, generate_stream_token
from common.schemas import ApiResponse
from core.database.session import get_session

router = APIRouter(tags=["Chat"])


@router.post("/chat/token", response_model=ApiResponse)
async def create_stream_token(
    current_user: Annotated[User, Depends(get_current_chat_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ApiResponse:
    try:
        data = await generate_stream_token(current_user, db)
    except StreamChatError as exc:
        return ApiResponse(status=False, message=str(exc), data=None)

    return ApiResponse(
        message="Stream token generated successfully.",
        data=data.model_dump(),
    )
