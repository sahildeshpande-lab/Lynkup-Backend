from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from core.database import get_session
from core.security.auth import get_current_user
from common.pagination import paginate_items
from apps.connections.services import (
    block_user as block_user_service,
    follow_user as follow_user_service,
    get_pending_requests as get_pending_requests_service,
    get_recommendations,
    respond_connection_request as respond_connection_request_service,
    send_connection_request,
    unblock_user as unblock_user_service,
    unfollow_user as unfollow_user_service,
)
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
    return await send_connection_request(db, current_user.id, UUID(request.receiver_user_id))


@router.post("/lynkupresponse", response_model=ApiResponse)
async def respond_connection_request(
    request: ConnectionRequestRespond,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await respond_connection_request_service(db, current_user.id, UUID(request.receiver_user_id), request.response)


@router.post("/follow", response_model=ApiResponse)
async def follow_user(
    payload: FollowRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await follow_user_service(db, current_user.id, UUID(payload.following_user_id))


@router.delete("/follow", response_model=ApiResponse)
async def unfollow_user(
    payload: FollowRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await unfollow_user_service(db, current_user.id, UUID(payload.following_user_id))


@router.post("/block", response_model=ApiResponse)
async def block_user(
    payload: BlockRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await block_user_service(db, current_user.id, UUID(payload.blocked_user_id))


@router.delete("/block", response_model=ApiResponse)
async def unblock_user(
    payload: BlockRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await unblock_user_service(db, current_user.id, UUID(payload.blocked_user_id))


@router.get("/recommendations/connections", response_model=ApiResponse)
async def get_connection_recommendations(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: int | None = Query(None, ge=1),
    pageSize: int | None = Query(None, ge=1, le=200),
):
    candidates = await get_recommendations(db, current_user.id)
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
    return await get_pending_requests_service(
        db,
        current_user.id,
        page=page,
        page_size=pageSize,
        search=search
    )
