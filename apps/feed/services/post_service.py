from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from common.enums import PostState
from apps.accounts.db_models import User
from apps.feed.db_models import Post
from apps.feed.schemas import SavePostRequest, EditPostRequest
from apps.feed.content_utils import sanitize_html, validate_content, validate_media_count
from core.images import generate_download_url

from .media_service import _verify_and_attach_media
from .revision_service import _build_content_dict, _create_revision, _sync_hashtags

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def format_post_detail(post: Post) -> dict:
    """
    Format a Post model and its attachments into a dictionary matching PostDetailData schema.
    """
    media_data = []
    if post.attachments:
        for attachment in post.attachments:
            asset = attachment.media_asset
            if asset:
                media_data.append({
                    "id": asset.id,
                    "key": asset.key,
                    "type": asset.type,
                    "url": generate_download_url(asset.key),
                    "original_filename": asset.original_filename,
                    "mime_type": asset.mime_type,
                    "file_size": asset.file_size
                })

    content = post.content or {}
    return {
        "id": post.id,
        "author_user_id": post.author_user_id,
        "state": post.state.value if hasattr(post.state, "value") else str(post.state),
        "revision_number": post.revision_number,
        "content": {
            "caption": content.get("caption"),
            "content_html": content.get("content_html"),
            "visibility": content.get("visibility", "public"),
        },
        "created_at": post.created_at,
        "updated_at": post.updated_at,
        "media": media_data
    }

async def save_post_service(
    user_id: UUID,
    payload: SavePostRequest,
    db: AsyncSession
) -> Post:
    """
    Unified create-or-update draft service.

    - If ``payload.id`` is ``None``: create a new draft (revision_number = 1).
    - If ``payload.id`` is provided: update the existing draft (revision_number += 1).

    In both paths the service validates content, sanitises HTML, manages
    attachment mappings, synchronises hashtags, and writes an immutable
    PostRevision audit snapshot.
    """
    # Validate media count
    try:
        validate_media_count(payload.media)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )

    # Build content dict (includes HTML sanitization)
    content_dict = _build_content_dict(payload.content)

    # Validate content
    has_media = bool(payload.media)
    try:
        validate_content(content_dict, has_media)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )

    # Determine state from visibility
    post_state = PostState.hidden if payload.content.visibility == "hidden" else PostState.draft

    # ----- CREATE -----
    if payload.id is None:
        post = Post(
            author_user_id=user_id,
            content=content_dict,
            state=post_state,
            revision_number=1,
        )
        db.add(post)

        try:
            await db.flush()

            if payload.media:
                await _verify_and_attach_media(
                    post_id=post.id,
                    user_id=user_id,
                    media_items=payload.media,
                    db=db,
                    replace=False,
                )

            await _sync_hashtags(post.id, content_dict.get("content_html"), db)
            await _create_revision(post, user_id, db)

            await db.commit()
            await db.refresh(post)
        except HTTPException:
            await db.rollback()
            raise
        except Exception as e:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create post: {str(e)}"
            )

        return post

    # ----- UPDATE -----
    result = await db.execute(select(Post).where(Post.id == payload.id))
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    if post.author_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Post does not belong to the authenticated user"
        )

    # Only drafts and hidden posts are editable through this endpoint
    if post.state not in (PostState.draft, PostState.hidden):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Post is in '{post.state.value}' state and cannot be edited as draft"
        )

    post.content = content_dict
    post.state = post_state
    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        if payload.media is not None:
            await _verify_and_attach_media(
                post_id=post.id,
                user_id=user_id,
                media_items=payload.media,
                db=db,
                replace=True,
            )

        await _sync_hashtags(post.id, content_dict.get("content_html"), db)
        await _create_revision(post, user_id, db)

        await db.commit()
        await db.refresh(post)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update post: {str(e)}"
        )

    return post

async def edit_post_service(
    user_id: UUID,
    payload: EditPostRequest,
    db: AsyncSession
) -> Post:
    """
    Edit/update fields of an existing post. All fields (and content fields) are optional.
    """
    result = await db.execute(select(Post).where(Post.id == payload.id))
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    if post.author_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Post does not belong to the authenticated user"
        )

    # Validate media count if media payload is provided
    if payload.media is not None:
        try:
            validate_media_count(payload.media)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc)
            )

    # Build updated/merged content dict
    merged_content = dict(post.content or {})
    if payload.content is not None:
        if payload.content.caption is not None:
            merged_content["caption"] = payload.content.caption
        if payload.content.content_html is not None:
            merged_content["content_html"] = sanitize_html(payload.content.content_html)
        if payload.content.visibility is not None:
            merged_content["visibility"] = payload.content.visibility

    # Validate final merged content against final media state
    if payload.media is not None:
        has_media = len(payload.media) > 0
    else:
        has_media = len(post.attachments) > 0

    try:
        validate_content(merged_content, has_media)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc)
        )

    # Apply changes to model
    post.content = merged_content

    # Determine state from visibility if provided
    if payload.content is not None and payload.content.visibility is not None:
        if payload.content.visibility == "hidden":
            post.state = PostState.hidden
        elif payload.content.visibility == "public" and post.state == PostState.hidden:
            post.state = PostState.draft

    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        # Manage media attachments if provided
        if payload.media is not None:
            await _verify_and_attach_media(
                post_id=post.id,
                user_id=user_id,
                media_items=payload.media,
                db=db,
                replace=True,
            )

        # Re-sync hashtags from current content_html
        content_html = merged_content.get("content_html")
        await _sync_hashtags(post.id, content_html, db)

        # Create revision audit record
        await _create_revision(post, user_id, db)

        await db.commit()
        await db.refresh(post)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to edit post: {str(e)}"
        )

    return post

async def publish_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession
) -> Post:
    """
    Publish a post.

    State transitions:
    - draft   → published
    - hidden  → hidden  (stays hidden)

    If visibility inside content is ``"hidden"`` the post stays ``PostState.hidden``.
    """
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    if post.author_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Post does not belong to the authenticated user"
        )

    # Only draft or hidden posts can be published
    if post.state not in (PostState.draft, PostState.hidden):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Post is in '{post.state.value}' state and cannot be published"
        )

    # State transition: respect visibility stored in content
    visibility = (post.content or {}).get("visibility", "public")
    if visibility == "hidden" or post.state == PostState.hidden:
        post.state = PostState.hidden
    else:
        post.state = PostState.published

    # Increment revision number and update timestamp
    post.revision_number += 1
    post.updated_at = utc_now()

    try:
        # Create revision audit record
        await _create_revision(post, user_id, db)

        await db.commit()
        await db.refresh(post)
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to publish post: {str(e)}"
        )

    return post

async def get_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession
) -> Post:
    """
    Retrieve details of a specific post.
    Validates visibility access permissions.
    """
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )

    # Visibility control: draft and hidden posts are private to the author
    if post.state in (PostState.draft, PostState.hidden):
        if post.author_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Post is not accessible"
            )

    return post

async def delete_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession
) -> None:
    """
    Hard delete a post from the database.
    """
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    if post.author_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Post does not belong to the authenticated user"
        )

    try:
        await db.delete(post)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete post: {str(e)}"
        )

async def list_user_posts_service(
    target_user_id: UUID,
    current_user_id: UUID,
    db: AsyncSession
) -> list[Post]:
    """
    List all posts of a specific user.
    Owner gets all posts, other users get only published posts.
    """
    # Verify user exists
    user_result = await db.execute(select(User).where(User.id == target_user_id))
    if not user_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    stmt = select(Post).where(Post.author_user_id == target_user_id)
    if target_user_id != current_user_id:
        stmt = stmt.where(Post.state == PostState.published)

    stmt = stmt.order_by(Post.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())
