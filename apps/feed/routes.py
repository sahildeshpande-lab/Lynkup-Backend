from __future__ import annotations

from fastapi import APIRouter, Depends, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from core.database.session import get_session
from core.security.auth import get_current_user
from apps.accounts.db_models import User
from common.enums import MediaType
from apps.feed.schemas import ApiResponse, SavePostRequest, DeletePostRequest, EditPostRequest
from apps.feed.services import (
    upload_post_media_service,
    save_post_service,
    edit_post_service,
    publish_post_service,
    get_post_service,
    delete_post_service,
    list_user_posts_service,
    get_feed_service,
    format_post_detail,
)

router = APIRouter(tags=["6] Feed / Posts"])


@router.post("/postupload", response_model=ApiResponse)
async def upload_post_media(
    file: UploadFile = File(...),
    type: MediaType = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """
    Upload a single media file before creating a post.
    """
    data = await upload_post_media_service(
        user_id=current_user.id,
        file=file,
        media_type=type,
        db=db
    )
    return ApiResponse(
        status=True,
        message=f"{type.value} uploaded",
        data=data
    )


@router.post("/post", response_model=ApiResponse)
async def save_post(
    payload: SavePostRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    post = await save_post_service(
        user_id=current_user.id,
        payload=payload,
        db=db
    )
    return ApiResponse(
        status=True,
        message="Post created successfully" if payload.id is None else "Post updated and saved as draft",
        data={
            "id": post.id,
            "revision_number": post.revision_number
        }
    )


@router.post("/posts/{id}/publish", response_model=ApiResponse)
async def publish_post(
    id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """
    Publish a post.
    """
    post = await publish_post_service(
        post_id=id,
        user_id=current_user.id,
        db=db
    )
    return ApiResponse(
        status=True,
        message="Post published",
        data=format_post_detail(post)
    )


@router.get("/posts/{id}", response_model=ApiResponse)
async def get_post(
    id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """
    Retrieve details of a post.
    """
    post = await get_post_service(
        post_id=id,
        user_id=current_user.id,
        db=db
    )
    return ApiResponse(
        status=True,
        message="Post retrieved successfully",
        data=format_post_detail(post)
    )


@router.patch("/posts", response_model=ApiResponse)
async def edit_post(
    payload: EditPostRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """
    Edit/update an existing post. All fields (caption, content_html, visibility, media) are optional.
    """
    post = await edit_post_service(
        user_id=current_user.id,
        payload=payload,
        db=db
    )
    return ApiResponse(
        status=True,
        message="Post updated successfully",
        data=format_post_detail(post)
    )


@router.delete("/posts", response_model=ApiResponse)
async def delete_post(
    payload: DeletePostRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """
    Hard delete a post.
    """
    await delete_post_service(
        post_id=payload.id,
        user_id=current_user.id,
        db=db
    )
    return ApiResponse(
        status=True,
        message="Post deleted successfully"
    )


@router.get("/posts", response_model=ApiResponse)
async def list_user_posts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """
    List posts for the authenticated user.
    """
    posts = await list_user_posts_service(
        target_user_id=current_user.id,
        current_user_id=current_user.id,
        db=db
    )
    return ApiResponse(
        status=True,
        message="User posts retrieved successfully",
        data=[format_post_detail(p) for p in posts]
    )


@router.get("/feed", response_model=ApiResponse)
async def get_feed(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """
    Get the feed.
    """
    posts = await get_feed_service(
        current_user_id=current_user.id,
        db=db
    )
    return ApiResponse(
        status=True,
        message="Feed retrieved successfully",
        data=[format_post_detail(p) for p in posts]
    )
