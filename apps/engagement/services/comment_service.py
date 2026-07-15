from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from common.pagination import build_paginated_response

from apps.engagement.config import settings
from apps.engagement.db_models import Comment
from apps.engagement.repositories.comment_repository import (
    create_comment,
    fetch_comments_by_parent_ids,
    fetch_profiles_by_user_ids,
    fetch_top_level_comments,
    get_comment_by_id,
    get_comment_for_update,
    increment_reply_count,
    mark_comment_deleted,
    count_top_level_comments,
    update_post_comment_count,
    post_exists,
)
from apps.engagement.repositories.comment_reaction_repository import (
    fetch_user_comment_reactions,
)
from apps.engagement.schemas import (
    CommentAuthor,
    CommentData,
    CommentListData,
    CommentListResponse,
    CommentResponse,
    CreateCommentRequest,
    DeleteCommentRequest,
)
from apps.engagement.services.author_service import format_engagement_author
from apps.engagement.services.reaction_service import format_user_reaction
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from common.responses import error_response, success_response


def _format_author(
    profile: Profile | None,
    university: University | None,
):
    return format_engagement_author(profile, university)


def _format_comment(
    comment: Comment,
    *,
    author: CommentAuthor,
    user_reaction: str | None,
    current_user_id: UUID,
    replies: list[CommentData] | None = None,
) -> CommentData:
    return CommentData(
        id=comment.id,
        post_id=comment.post_id,
        parent_comment_id=comment.parent_comment_id,
        level=comment.level,
        is_deleted=comment.is_deleted,
        like_count=comment.like_count,
        reply_count=comment.reply_count,
        comment_text=comment.comment_text,
        author=author,
        user_reaction=user_reaction,
        can_delete_comment=comment.user_id == current_user_id,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        replies=replies or [],
    )


async def _fetch_all_descendants(
    db: AsyncSession,
    root_ids: list[UUID],
) -> list[Comment]:
    descendants: list[Comment] = []
    current_parent_ids = list(root_ids)

    for _ in range(settings.comment_max_depth):
        if not current_parent_ids:
            break
        children = await fetch_comments_by_parent_ids(db, current_parent_ids)
        if not children:
            break
        descendants.extend(children)
        current_parent_ids = [child.id for child in children]

    return descendants


def _build_reply_tree(
    parent_id: UUID,
    descendants: list[Comment],
    profiles_by_user: dict[UUID, tuple[Profile | None, University | None]],
    reactions_by_comment: dict[UUID, str | None],
    current_user_id: UUID,
) -> list[CommentData]:
    by_parent: dict[UUID, list[Comment]] = defaultdict(list)
    for comment in descendants:
        if comment.parent_comment_id is not None:
            by_parent[comment.parent_comment_id].append(comment)

    def build_level(comment: Comment) -> CommentData:
        profile, university = profiles_by_user.get(comment.user_id, (None, None))
        return _format_comment(
            comment,
            author=_format_author(profile, university),
            user_reaction=reactions_by_comment.get(comment.id),
            current_user_id=current_user_id,
            replies=[
                build_level(child)
                for child in sorted(by_parent.get(comment.id, []), key=lambda item: item.created_at)
            ],
        )

    return [
        build_level(child)
        for child in sorted(by_parent.get(parent_id, []), key=lambda item: item.created_at)
    ]


async def create_post_comment(
    db: AsyncSession,
    user_id: UUID,
    payload: CreateCommentRequest,
) -> CommentResponse:
    post_id = payload.post_id
    if not await post_exists(db, post_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    text = payload.comment_text.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Comment text is required")

    parent: Comment | None = None

    if payload.parent_comment_id is None:
        level = 1
    else:
        parent = await get_comment_by_id(db, payload.parent_comment_id)
        if parent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent comment not found")
        if parent.post_id != post_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent comment does not belong to this post",
            )
        if parent.level >= settings.comment_max_depth:
            return error_response(
                f"Maximum nesting depth of {settings.comment_max_depth} exceeded",
                response_cls=CommentResponse,
            )
        level = parent.level + 1

    try:
        comment = await create_comment(
            db,
            post_id=post_id,
            user_id=user_id,
            comment_text=text,
            parent_comment_id=payload.parent_comment_id,
            level=level,
        )
        if parent is not None:
            await increment_reply_count(db, parent.id)
        elif payload.parent_comment_id is None:
            await update_post_comment_count(db, post_id, 1)
        await db.commit()
        await db.refresh(comment)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create comment",
        )

    profiles = await fetch_profiles_by_user_ids(db, [user_id])
    profile, university = profiles.get(user_id, (None, None))
    new_comment = _format_comment(
        comment,
        author=_format_author(profile, university),
        user_reaction=None,
        current_user_id=user_id,
    )

    return success_response(
        "Comment created successfully",
        new_comment,
        response_cls=CommentResponse,
    )


async def get_post_comments(
    db: AsyncSession,
    user_id: UUID,
    post_id: UUID,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> CommentListResponse:
    if not await post_exists(db, post_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    total = await count_top_level_comments(db, post_id)
    if page is not None and page_size is not None:
        offset = (page - 1) * page_size
        limit = page_size
        resolved_page = page
        resolved_page_size = page_size
    else:
        offset = 0
        limit = None
        resolved_page = 1
        resolved_page_size = total if total > 0 else 1

    top_level_rows = await fetch_top_level_comments(
        db, post_id, offset=offset, limit=limit
    )
    root_ids = [comment.id for comment, _, _ in top_level_rows]

    descendants = await _fetch_all_descendants(db, root_ids)
    all_comments = [comment for comment, _, _ in top_level_rows] + descendants
    user_ids = list({comment.user_id for comment in all_comments})
    comment_ids = [comment.id for comment in all_comments]

    profiles_by_user = await fetch_profiles_by_user_ids(db, user_ids)
    reactions_raw = await fetch_user_comment_reactions(db, user_id, comment_ids)
    reactions_by_comment = {
        comment_id: format_user_reaction(reaction_type)
        for comment_id, reaction_type in reactions_raw.items()
    }

    comments: list[CommentData] = []
    for comment, profile, university in top_level_rows:
        comments.append(
            _format_comment(
                comment,
                author=_format_author(profile, university),
                user_reaction=reactions_by_comment.get(comment.id),
                current_user_id=user_id,
                replies=_build_reply_tree(
                    comment.id,
                    descendants,
                    profiles_by_user,
                    reactions_by_comment,
                    user_id,
                ),
            )
        )

    paginated = build_paginated_response(
        comments,
        resolved_page,
        resolved_page_size,
        total,
    )
    return success_response(
        "Comments fetched successfully",
        CommentListData(
            comments=list(paginated.items),
            page=paginated.page,
            pageSize=paginated.pageSize,
            totalItems=paginated.totalItems,
            totalPages=paginated.totalPages,
        ),
        response_cls=CommentListResponse,
    )


async def delete_comment(
    db: AsyncSession,
    user_id: UUID,
    payload: DeleteCommentRequest,
) -> CommentResponse:
    comment = await get_comment_for_update(db, payload.comment_id)
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    if comment.post_id != payload.post_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Comment does not belong to this post",
        )
    if comment.user_id != user_id:
        return error_response(
            "Not the authenticated user",
            response_cls=CommentResponse,
        )
    if comment.is_deleted:
        profiles = await fetch_profiles_by_user_ids(db, [comment.user_id])
        profile, university = profiles.get(comment.user_id, (None, None))
        return success_response(
            "Comment deleted successfully",
            _format_comment(
                comment,
                author=_format_author(profile, university),
                user_reaction=None,
                current_user_id=user_id,
            ),
            response_cls=CommentResponse,
        )

    try:
        if comment.parent_comment_id is None:
            await update_post_comment_count(db, comment.post_id, -1)
        await mark_comment_deleted(db, comment)
        await db.commit()
        await db.refresh(comment)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete comment",
        )

    profiles = await fetch_profiles_by_user_ids(db, [comment.user_id])
    profile, university = profiles.get(comment.user_id, (None, None))

    return success_response(
        "Comment deleted successfully",
        _format_comment(
            comment,
            author=_format_author(profile, university),
            user_reaction=None,
            current_user_id=user_id,
        ),
        response_cls=CommentResponse,
    )
