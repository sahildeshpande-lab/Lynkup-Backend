from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from core.database.session import get_session
from core.security.auth import (
    get_current_app_user,
    get_current_user,
    get_current_user_moderator_or_superadmin,
)
from apps.accounts.db_models import User
from common.enums import MediaType
from common.responses import success_response
from apps.feed.schemas import ApiResponse, PostUploadResponse, SavePostRequest, DeletePostRequest, EditPostRequest
from apps.feed.services import (
    upload_post_media_service,
    save_post_service,
    edit_post_service,
    publish_post_service,
    get_post_service,
    build_post_detail_response,
    delete_post_service,
    list_draft_posts_service,
    delete_draft_post_service,
    list_user_posts_items_service,
    get_profile_visibility_block_message,
    get_feed_service,
    format_post_detail,
    list_post_revisions_service,
)

router = APIRouter(tags=["6] Feed / Posts"])


@router.post("/postupload", response_model=PostUploadResponse)
async def upload_post_media(
    files: list[UploadFile] = File(
        ...,
        description="Upload one or more media files using repeated form field name 'files'.",
    ),
    types: list[MediaType] | None = Form(default=None),
    current_user: User = Depends(get_current_app_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await upload_post_media_service(
        user_id=current_user.id,
        files=files,
        media_types=types,
        db=db,
    )
    return success_response(
        f"{len(data)} media file(s) uploaded",
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
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    post = await get_post_service(
        post_id=id,
        user_id=current_user.id,
        db=db
    )
    post_data = await build_post_detail_response(
        db,
        post,
        viewer_user_id=current_user.id,
    )
    return success_response(
        "Post retrieved successfully",
        post_data,
        response_cls=ApiResponse,
    )


@router.get("/postrevision", response_model=ApiResponse)
async def list_post_revisions(
    post_id: UUID = Query(..., description="Post id to fetch content revisions for"),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Return every content revision snapshot for a post (newest first)."""
    _ = current_user
    data = await list_post_revisions_service(db, post_id)
    return success_response(
        "Post revisions fetched successfully",
        data,
        response_cls=ApiResponse,
    )


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
    return success_response(
        "Post updated successfully",
        format_post_detail(post, viewer_user_id=current_user.id),
        response_cls=ApiResponse,
    )


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
        description=(
            "Default (published): published + reinstate. "
            "flagged: flagged + processing. "
            "processing/draft: that state only. "
            "Owner or staff (moderator/viewer/superadmin) may request flagged/processing "
            "for a user_id; regular visitors are limited to published."
        ),
        enum=["published", "flagged", "processing", "draft"],
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
        return success_response(
            "Account is private",
            [],
            response_cls=ApiResponse,
        )

    posts, total_items = await list_user_posts_items_service(
        current_user=current_user,
        target_user_id=user_id,
        state=state,
        page=page,
        page_size=pageSize,
        db=db,
    )
    formatted_posts = posts
    if page is None and pageSize is None:
        return success_response(
            "User posts retrieved successfully",
            formatted_posts,
            response_cls=ApiResponse,
        )
    from common.pagination import build_paginated_response
    p = page or 1
    ps = pageSize if pageSize is not None else (total_items if total_items > 0 else 1)
    paginated = build_paginated_response(
        formatted_posts,
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
        [format_post_detail(p, viewer_user_id=current_user.id) for p in posts],
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
    response: Response,
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    cursor: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    formatted_posts, total_items, next_cursor = await get_feed_service(
        current_user_id=current_user.id,
        page=page,
        page_size=pageSize,
        cursor=cursor,
        db=db,
        include_total=True,
    )
    if next_cursor:
        # Keep JSON body identical; expose keyset cursor out-of-band for clients that want it.
        response.headers["X-Next-Cursor"] = next_cursor
    if page is None and pageSize is None and cursor is None:
        return success_response(
            "Feed retrieved successfully",
            formatted_posts,
            response_cls=ApiResponse,
        )
    from common.pagination import build_paginated_response
    p = page or 1
    ps = pageSize if pageSize is not None else (total_items if total_items > 0 else 1)
    paginated = build_paginated_response(
        formatted_posts,
        p,
        ps,
        total_items
    )
    return success_response(
        "Feed retrieved successfully",
        paginated,
        response_cls=ApiResponse,
    )
s