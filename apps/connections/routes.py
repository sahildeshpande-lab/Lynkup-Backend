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
    FollowRequest,
    BlockRequest,
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
    return await services.send_connection_request(db, current_user.id, UUID(request.receiver_user_id))


@router.post("/lynkupresponse", response_model=ApiResponse)
async def respond_connection_request(
    request: ConnectionRequestRespond,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.respond_connection_request(db, current_user.id, UUID(request.receiver_user_id), request.response)


@router.post("/follows", response_model=ApiResponse)
async def follow_user(
    payload: FollowRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.follow_user(db, current_user.id, UUID(payload.following_user_id))


@router.delete("/follows", response_model=ApiResponse)
async def unfollow_user(
    payload: FollowRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.unfollow_user(db, current_user.id, UUID(payload.following_user_id))


@router.post("/block", response_model=ApiResponse)
async def block_user(
    payload: BlockRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.block_user(db, current_user.id, UUID(payload.blocked_user_id))


@router.delete("/block", response_model=ApiResponse)
async def unblock_user(
    payload: BlockRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await services.unblock_user(db, current_user.id, UUID(payload.blocked_user_id))


@router.get("/recommendations/connections", response_model=ApiResponse)
async def get_connection_recommendations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: int | None = Query(None, ge=1),
    pageSize: int | None = Query(None, ge=1, le=200),
):
    candidates = await services.get_recommendations(db, current_user.id)
    items = [RecommendedUserResponse(**item) for item in candidates]
    
    if page is None and pageSize is None:
        # if not provided: ALL
        return ApiResponse(data=items)
        
    p = page or 1
    ps = pageSize or 20
    paginated = paginate_items(items, page=p, page_size=ps)
    return ApiResponse(data=paginated.model_dump())


@router.get("/lynkup", response_model=ApiResponse)
async def get_pending_requests(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: int | None = Query(None, ge=1),
    pageSize: int | None = Query(None, ge=1, le=200),
    search: str | None = Query(None),
):
    return await services.get_pending_requests(
        db,
        current_user.id,
        page=page,
        page_size=pageSize,
        search=search
    )
