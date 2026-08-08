from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from apps.accounts.db_models import User
from apps.chat.service import StreamChatError, generate_stream_token
from common.enums import UserStatus, inactive_account_message
from common.exceptions import ApiError
from common.schemas import ApiResponse
from core.auth.firebase import get_current_firebase_user
from core.database.session import get_session

router = APIRouter(tags=["Chat"])


async def get_current_chat_user(
    firebase_user: Annotated[dict, Depends(get_current_firebase_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    firebase_uid = firebase_user.get("uid")
    if not firebase_uid:
        raise ApiError("Invalid Firebase credentials")

    stmt = select(User).options(selectinload(User.roles)).where(User.firebase_uid == firebase_uid)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        raise ApiError("User not found")

    if user.status == UserStatus.deleting or user.deleted_at:
        raise ApiError(inactive_account_message(UserStatus.deleting))
    if user.status in (UserStatus.suspended, UserStatus.banned):
        raise ApiError(inactive_account_message(user.status))
    if user.status not in (UserStatus.active, UserStatus.pending):
        raise ApiError(inactive_account_message(user.status))

    return user


@router.post("/chat/token", response_model=ApiResponse)
async def create_stream_token(
    current_user: Annotated[User, Depends(get_current_chat_user)],
) -> ApiResponse:
    try:
        data = await generate_stream_token(current_user)
    except StreamChatError as exc:
        return ApiResponse(status=False, message=str(exc), data=None)

    return ApiResponse(
        message="Stream token generated successfully.",
        data=data.model_dump(),
    )
