from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from core.database import get_session
from core.security.auth import get_current_user
from common.pagination import paginate_items, PaginatedResponse
from . import services
from .schemas import (
    ApiResponse,
    ConnectionRequestCreate,
    ConnectionRequestRespond,
    ConnectionRequestResponse,
    FollowResponse,
    BlockResponse,
    RecommendedUserResponse,
)

router = APIRouter(prefix="", tags=["5] Connection Managements"])


@router.post("/lynkuprequest", response_model=ApiResponse)
async def create_connection_request(
    request: ConnectionRequestCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.send_connection_request(db, current_user.id, request.receiver_user_id)


@router.post("/lynkupresponse", response_model=ApiResponse)
async def respond_connection_request(
    request: ConnectionRequestRespond,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.respond_connection_request(db, current_user.id, request.request_id, request.response)


@router.post("/follows/{user_id}", response_model=ApiResponse)
async def follow_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.follow_user(db, current_user.id, user_id)


@router.delete("/follows/{user_id}", response_model=ApiResponse)
async def unfollow_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.unfollow_user(db, current_user.id, user_id)


@router.post("/block/{user_id}", response_model=ApiResponse)
async def block_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.block_user(db, current_user.id, user_id)


@router.delete("/block/{user_id}", response_model=ApiResponse)
async def unblock_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.unblock_user(db, current_user.id, user_id)


@router.get("/recommendations/connections", response_model=ApiResponse)
async def get_connection_recommendations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
):
    candidates = await services.get_recommendations(db, current_user.id)
    if not candidates:
        paginated = paginate_items([], page, pageSize)
        return ApiResponse(data=paginated)
        
    paginated = paginate_items(candidates, page, pageSize)
    
    return ApiResponse(data=PaginatedResponse[RecommendedUserResponse](
        items=[RecommendedUserResponse(**item) for item in paginated.items],
        page=paginated.page,
        pageSize=paginated.pageSize,
        totalItems=paginated.totalItems,
        totalPages=paginated.totalPages
    ))


@router.get("/lynkup", response_model=ApiResponse)
async def get_pending_requests(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.get_pending_requests(db, current_user.id)
