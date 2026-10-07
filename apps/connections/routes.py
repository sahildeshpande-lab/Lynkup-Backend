from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from core.database import get_session
from core.security.auth import get_current_user
from core.security.mobile.dependencies import require_mobile_request_security
from common.pagination import paginate_items, paginate_or_all
from common.responses import error_response, success_response
from apps.connections.services import (
    block_user as block_user_service,
    follow_user as follow_user_service,
    get_pending_requests as get_pending_requests_service,
    get_connections_service,
    get_mutual_recommendations,
    get_recommendations,
    get_recommendations_categorized,
    remove_connection as remove_connection_service,
    respond_connection_request as respond_connection_request_service,
    send_connection_request,
    unblock_user as unblock_user_service,
    unfollow_user as unfollow_user_service,
)
from .schemas import (
    ApiResponse,
    ConnectionRequestCreate,
    ConnectionRemoveRequest,
    ConnectionRequestRespond,
    FollowRequest,
    BlockRequest,
    ConnectionRequestResponse,
    FollowResponse,
    BlockResponse,
    MutualRecommendedUserResponse,
    RecommendedUserResponse,
)

router = APIRouter(prefix="", tags=["5] Connection Managements"])


@router.post("/lynkuprequest", response_model=ApiResponse)
async def create_connection_request(
    request: ConnectionRequestCreate,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await send_connection_request(db, current_user.id, UUID(request.receiver_user_id))


@router.post("/lynkupresponse", response_model=ApiResponse)
async def respond_connection_request(
    request: ConnectionRequestRespond,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    try:
        other_user_id = UUID(request.receiver_user_id)
    except ValueError:
        return error_response("Not a valid User", response_cls=ApiResponse)
    return await respond_connection_request_service(
        db, current_user.id, other_user_id, request.response
    )


@router.delete("/lynkupremove", response_model=ApiResponse)
async def remove_connection(
    request: ConnectionRemoveRequest,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ApiResponse:
    return await remove_connection_service(db, current_user.id, request.user_id)


@router.post("/follow", response_model=ApiResponse)
async def follow_user(
    payload: FollowRequest,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await follow_user_service(db, current_user.id, UUID(payload.following_user_id))


@router.delete("/follow", response_model=ApiResponse)
async def unfollow_user(
    payload: FollowRequest,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await unfollow_user_service(db, current_user.id, UUID(payload.following_user_id))


@router.post("/block", response_model=ApiResponse)
async def block_user(
    payload: BlockRequest,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await block_user_service(db, current_user.id, UUID(payload.blocked_user_id))


@router.delete("/block", response_model=ApiResponse)
async def unblock_user(
    payload: BlockRequest,
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
):
    return await unblock_user_service(db, current_user.id, UUID(payload.blocked_user_id))


@router.get("/recommendations/connections", response_model=ApiResponse)
async def get_connection_recommendations(
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: int | None = Query(None, ge=1),
    pageSize: int | None = Query(None, ge=1, le=200),
):
    rec_result = await get_recommendations_categorized(db, current_user.id)
    items = [RecommendedUserResponse(**item) for item in rec_result["items"]]
    # based_on_major_minor = [RecommendedUserResponse(**item) for item in rec_result["based_on_major_minor"]]
    # without_major_minor = [RecommendedUserResponse(**item) for item in rec_result["without_major_minor"]]

    if page is None and pageSize is None:
        data = {
            "items": items,
            # "based_on_major_minor": based_on_major_minor,
            # "without_major_minor": without_major_minor,
            "page": 1,
            "pageSize": len(items),
            "totalItems": len(items),
            "totalPages": 1,
        }
    else:
        p = page or 1
        ps = pageSize or 20
        paginated = paginate_items(items, page=p, page_size=ps)
        data = paginated.model_dump()
        # data["based_on_major_minor"] = [b.model_dump() for b in based_on_major_minor]
        # data["without_major_minor"] = [w.model_dump() for w in without_major_minor]

    return success_response("Connection recommendations fetched", data, response_cls=ApiResponse)


@router.get("/connections/mutual-recommendations", response_model=ApiResponse)
async def get_mutual_connection_recommendations(
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: int | None = Query(None, ge=1),
    pageSize: int | None = Query(None, ge=1, le=200),
):
    rec_items = await get_mutual_recommendations(db, current_user.id)
    items = [MutualRecommendedUserResponse(**item) for item in rec_items]
    paginated = paginate_or_all(items, page, pageSize)
    return success_response(
        "Mutual connection recommendations fetched",
        paginated.model_dump(),
        response_cls=ApiResponse,
    )


@router.get("/connections", response_model=ApiResponse)
async def list_connections(
    current_user: Annotated[User, Depends(require_mobile_request_security)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: int | None = Query(None, ge=1),
    pageSize: int | None = Query(None, ge=1, le=200),
):
    return await get_connections_service(
        db,
        current_user.id,
        page=page,
        page_size=pageSize,
    )


@router.get("/lynkup", response_model=ApiResponse)
async def get_pending_requests(
    current_user: Annotated[User, Depends(require_mobile_request_security)],
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
