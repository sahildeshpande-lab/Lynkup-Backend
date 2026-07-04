from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from core.database.session import get_session
from core.security.auth import get_current_app_user, get_current_user
from apps.accounts.db_models import User
from common.responses import success_response
from apps.feed.schemas import ApiResponse, PostUploadResponse, SavePostRequest, DeletePostRequest, EditPostRequest
from apps.feed.services import (
    upload_post_media_service,
    save_post_service,
    edit_post_service,
    publish_post_service,
    get_post_service,
    delete_post_service,
    list_draft_posts_service,
    delete_draft_post_service,
    list_user_posts_service,
    get_profile_visibility_block_message,
    get_feed_service,
    format_post_detail,
)

router = APIRouter(tags=["6] Feed / Posts"])


@router.post("/postupload", response_model=PostUploadResponse)
async def upload_post_media(
    files: list[UploadFile] = File(
        ...,
        description="Upload one or more media files using repeated form field name 'files'.",
    ),
    current_user: User = Depends(get_current_app_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await upload_post_media_service(
        user_id=current_user.id,
        files=files,
        db=db,
    )
    return success_response(
        "Files uploaded successfully",
        data,
        response_cls=ApiResponse,
    )


@router.post("/post", response_model=ApiResponse)
async def save_post(
    payload: SavePostRequest,
    current_user: User = Depends(get_current_app_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    post = await save_post_service(
        user_id=current_user.id,
        payload=payload,
        db=db
    )
    if payload.id is None:
        message = "Post created and saved as draft" if payload.is_draft else "Post created successfully"
    else:
        message = "Post updated and saved as draft" if payload.is_draft else "Post updated and processing"
    return success_response(
        message,
        {"id": post.id, "revision_number": post.revision_number},
        response_cls=ApiResponse,
    )


@router.get("/posts/{id}", response_model=ApiResponse)
async def get_post(
    id: UUID,
    current_user: User = Depends(get_current_app_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    post = await get_post_service(
        post_id=id,
        user_id=current_user.id,
        db=db
    )
    return success_response("Post retrieved successfully", format_post_detail(post), response_cls=ApiResponse)


@router.patch("/posts", response_model=ApiResponse)
async def edit_post(
    payload: EditPostRequest,
    current_user: User = Depends(get_current_app_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    post = await edit_post_service(
        user_id=current_user.id,
        payload=payload,
        db=db
    )
    return success_response("Post updated successfully", format_post_detail(post), response_cls=ApiResponse)


@router.delete("/posts", response_model=ApiResponse)
async def delete_post(
    payload: DeletePostRequest,
    current_user: User = Depends(get_current_app_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    await delete_post_service(
        post_id=payload.id,
        user_id=current_user.id,
        db=db
    )
    return success_response("Post deleted successfully", response_cls=ApiResponse)


@router.get("/posts", response_model=ApiResponse)
async def list_user_posts(
    user_id: UUID | None = Query(default=None, description="Filter posts by user id"),
    state: str = Query(
        default="published",
        description="Filter posts by state",
        enum=["published", "processing", "flagged", "draft"],
    ),
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    block_message = await get_profile_visibility_block_message(
        current_user=current_user,
        target_user_id=user_id,
        db=db,
    )
    if block_message:
        from common.pagination import build_paginated_response
        return success_response(
            block_message,
            build_paginated_response([], 1, 1, 0),
            response_cls=ApiResponse,
        )

    posts, total_items = await list_user_posts_service(
        current_user=current_user,
        target_user_id=user_id,
        state=state,
        page=page,
        page_size=pageSize,
        db=db
    )
    from common.pagination import build_paginated_response
    p = page or 1
    ps = pageSize if pageSize is not None else (total_items if total_items > 0 else 1)
    paginated = build_paginated_response(
        [format_post_detail(post) for post in posts],
        p,
        ps,
        total_items
    )
    return success_response(
        "User posts retrieved successfully",
        paginated,
        response_cls=ApiResponse,
    )


@router.get("/draftpost", response_model=ApiResponse)
async def list_draft_posts(
    current_user: User = Depends(get_current_app_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    posts = await list_draft_posts_service(
        user_id=current_user.id,
        db=db,
    )
    return success_response(
        "Draft posts retrieved successfully",
        [format_post_detail(p) for p in posts],
        response_cls=ApiResponse,
    )


@router.delete("/draftpost", response_model=ApiResponse)
async def delete_draft_post(
    payload: DeletePostRequest,
    current_user: User = Depends(get_current_app_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    await delete_draft_post_service(
        post_id=payload.id,
        user_id=current_user.id,
        db=db,
    )
    return success_response("Draft post deleted successfully", response_cls=ApiResponse)


@router.get("/feed", response_model=ApiResponse)
async def get_feed(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    posts, total_items = await get_feed_service(
        current_user_id=current_user.id,
        page=page,
        page_size=pageSize,
        db=db
    )
    from common.pagination import build_paginated_response
    p = page or 1
    ps = pageSize if pageSize is not None else (total_items if total_items > 0 else 1)
    paginated = build_paginated_response(
        [format_post_detail(post) for post in posts],
        p,
        ps,
        total_items
    )
    return success_response(
        "Feed retrieved successfully",
        paginated,
        response_cls=ApiResponse,
    )
