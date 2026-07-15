from __future__ import annotations

from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.engagement.schemas import (
    BookmarkListResponse,
    BookmarkRequest,
    BookmarkResponse,
    CommentListResponse,
    CommentResponse,
    CommentReactionResponse,
    CreateCommentRequest,
    DeleteCommentRequest,
    LikedPostsListResponse,
    PostReactionResponse,
    PostReactionsListResponse,
    REACTION_TYPE_DESCRIPTION,
    RepostPostRequest,
    RepostResponse,
    SharePostRequest,
    ShareResponse,
    UpsertCommentReactionRequest,
    UpsertPostReactionRequest,
)
from apps.engagement.services import (
    create_post_comment,
    delete_comment,
    get_post_comments,
    get_post_reactions,
    list_bookmarked_posts,
    list_liked_posts,
    repost_post,
    share_post,
    update_bookmark,
    upsert_comment_reaction,
    upsert_post_reaction,
)
from common.enums import ReactionType
from core.database.session import get_session
from core.security.auth import get_current_app_user, get_current_user

router = APIRouter(tags=["7] Post Engagement"])


@router.post(
    "/posts/reactions",
    response_model=PostReactionResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert or remove a post reaction",
    description=(
        "Add, change, or remove the authenticated user's reaction on a post.\n\n"
        f"{REACTION_TYPE_DESCRIPTION}"
    ),
)
async def upsert_post_reaction_route(
    payload: UpsertPostReactionRequest,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PostReactionResponse:
    return await upsert_post_reaction(db, current_user.id, payload)


@router.post(
    "/posts/repost",
    response_model=RepostResponse,
    status_code=status.HTTP_200_OK,
)
async def create_post_repost(
    payload: RepostPostRequest,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> RepostResponse:
    return await repost_post(db, current_user.id, payload.post_id)


@router.post(
    "/posts/share",
    response_model=ShareResponse,
    status_code=status.HTTP_200_OK,
    summary="Share a post",
    description="Record a share event for the authenticated user's profile.",
)
async def share_post_route(
    payload: SharePostRequest,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ShareResponse:
    return await share_post(db, current_user.id, payload.post_id)


@router.get(
    "/posts/liked",
    response_model=LikedPostsListResponse,
    status_code=status.HTTP_200_OK,
    summary="List liked posts",
    description=(
        "Return posts the authenticated user has reacted to. "
        "Supports optional page and pageSize pagination; omit both to return all."
    ),
)
async def list_liked_posts_route(
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
) -> LikedPostsListResponse:
    return await list_liked_posts(
        db,
        current_user.id,
        page=page,
        page_size=pageSize,
    )


@router.get(
    "/posts/bookmark",
    response_model=BookmarkListResponse,
    status_code=status.HTTP_200_OK,
    summary="List bookmarked posts",
    description="Return paginated posts bookmarked by the authenticated user.",
)
async def list_post_bookmarks(
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
) -> BookmarkListResponse:
    return await list_bookmarked_posts(
        db,
        current_user.id,
        page=page,
        page_size=pageSize,
    )


@router.patch(
    "/posts/bookmark",
    response_model=BookmarkResponse,
    status_code=status.HTTP_200_OK,
)
async def update_post_bookmark(
    payload: BookmarkRequest,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> BookmarkResponse:
    return await update_bookmark(db, current_user.id, payload)



@router.get(
    "/posts/{post_id}/postreaction",
    response_model=PostReactionsListResponse,
    status_code=status.HTTP_200_OK,
    summary="List post reactions",
    description=(
        "Return reactors grouped by reaction type with summary counts. "
        "Accessible to app users, moderators, viewers, and superadmins. "
        "Optionally filter by reaction_type. "
        "Supports optional page and pageSize pagination; omit both to return all."
    ),
    include_in_schema=True,
)
async def list_post_reactions(
    post_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    reaction_type: ReactionType | None = Query(
        default=None,
        description=(
            "Optional reaction type filter. "
            f"Allowed values: {', '.join(rt.value for rt in ReactionType)}"
        ),
    ),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
) -> PostReactionsListResponse:
    _ = current_user
    return await get_post_reactions(
        db,
        post_id,
        reaction_type=reaction_type,
        page=page,
        page_size=pageSize,
    )


@router.post(
    "/posts/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a comment or reply",
    description=(
        "Create a top-level comment when parent_comment_id is omitted, "
        "or a nested reply when parent_comment_id is provided. "
        "Comment depth is derived server-side from the parent (parent level + 1). "
        "Returns the created comment. "
        "Nesting depth is capped by DEFAULT_COMMENT_MAX_DEPTH."
    ),
)
async def create_comment_route(
    payload: CreateCommentRequest,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CommentResponse:
    return await create_post_comment(db, current_user.id, payload)


@router.get(
    "/posts/{post_id}/comments",
    response_model=CommentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List post comments",
    description=(
        "Return top-level comments with nested replies up to the configured max depth. "
        "Supports optional page and pageSize pagination; omit both to return all. "
        "Accessible to app users, moderators, viewers, and superadmins."
    ),
)
async def list_post_comments(
    post_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
) -> CommentListResponse:
    return await get_post_comments(
        db,
        current_user.id,
        post_id,
        page=page,
        page_size=pageSize,
    )


@router.delete(
    "/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_200_OK,
    summary="Soft delete a comment",
    description=(
        "Marks the comment as deleted while preserving its original text. "
        "Only the comment author can delete. Replies remain attached."
    ),
)
async def delete_comment_route(
    payload: DeleteCommentRequest,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CommentResponse:
    return await delete_comment(db, current_user.id, payload)


@router.post(
    "/comments/reactions",
    response_model=CommentReactionResponse,
    status_code=status.HTTP_200_OK,
    summary="Upsert or remove a comment reaction",
    description="Add, change, or remove the authenticated user's reaction on a comment.",
)
async def upsert_comment_reaction_route(
    payload: UpsertCommentReactionRequest,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CommentReactionResponse:
    return await upsert_comment_reaction(db, current_user.id, payload)
