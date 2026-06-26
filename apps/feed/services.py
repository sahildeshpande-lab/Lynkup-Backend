from __future__ import annotations

import uuid
from pathlib import Path
from uuid import UUID
from fastapi import UploadFile, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from common.enums import MediaType, MediaAssetState, PostState
from apps.accounts.db_models import User
from apps.feed.db_models import Post, MediaAsset, PostAttachment
from apps.feed.schemas import CreatePostRequest, UpdatePostRequest
from core.images import save_image, generate_download_url


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
    return {
        "id": post.id,
        "author_user_id": post.author_user_id,
        "caption": post.caption,
        "content_html": post.content_html,
        "state": post.state.value if hasattr(post.state, "value") else str(post.state),
        "created_at": post.created_at,
        "updated_at": post.updated_at,
        "media": media_data
    }


async def upload_post_media_service(
    user_id: UUID,
    file: UploadFile,
    media_type: MediaType,
    db: AsyncSession
) -> dict:
    """
    Validate uploaded file, save it using the storage utility,
    and persist metadata in the MediaAsset table.
    """
    content = await file.read()
    file_size = len(content)

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot upload an empty file"
        )

    # Simple content type validation based on MediaType
    content_type = file.content_type or ""
    if media_type == MediaType.image and not content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type for image media asset"
        )
    elif media_type == MediaType.video and not content_type.startswith("video/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type for video media asset"
        )
    elif media_type == MediaType.audio and not content_type.startswith("audio/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type for audio media asset"
        )

    # Extract extension
    ext = Path(file.filename).suffix if file.filename else ""
    if not ext:
        if "jpeg" in content_type or "jpg" in content_type:
            ext = ".jpg"
        elif "png" in content_type:
            ext = ".png"
        elif "gif" in content_type:
            ext = ".gif"
        elif "mp4" in content_type:
            ext = ".mp4"
        elif "pdf" in content_type:
            ext = ".pdf"
        else:
            ext = ".bin"

    ext = ext.lower()
    if not ext.startswith("."):
        ext = f".{ext}"

    # Generate storage key: posts/<uuid>.<ext>
    file_uuid = uuid.uuid4()
    filename = f"{file_uuid}{ext}"
    key = f"posts/{filename}"

    # Save to storage (S3 or local depending on settings)
    try:
        save_image(file_name=key, content=content, content_type=content_type)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Storage upload failed: {str(e)}"
        )

    # Save media metadata in the database
    media_asset = MediaAsset(
        owner_user_id=user_id,
        key=key,
        type=media_type,
        original_filename=file.filename,
        mime_type=content_type,
        file_size=file_size,
        state=MediaAssetState.published,
    )
    db.add(media_asset)

    try:
        await db.commit()
        await db.refresh(media_asset)
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error saving media metadata: {str(e)}"
        )

    return {
        "id": media_asset.id,
        "key": media_asset.key,
        "type": media_asset.type,
        "url": generate_download_url(key)
    }


async def create_post_service(
    user_id: UUID,
    payload: CreatePostRequest,
    db: AsyncSession
) -> Post:
    """
    Create a new post in the database.
    Verifies media assets belong to the authenticated user.
    """
    post_state = PostState.hidden if payload.visibility == "hidden" else PostState.draft

    post = Post(
        author_user_id=user_id,
        caption=payload.caption,
        content_html=payload.text,
        state=post_state
    )
    db.add(post)

    try:
        # Flush to obtain post.id before creating attachments
        await db.flush()

        if payload.media:
            for media_item in payload.media:
                # Query and verify media asset ownership
                result = await db.execute(select(MediaAsset).where(MediaAsset.id == media_item.id))
                media_asset = result.scalar_one_or_none()
                if not media_asset:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Media asset with ID {media_item.id} not found"
                    )
                if media_asset.owner_user_id != user_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Media asset with ID {media_item.id} does not belong to the authenticated user"
                    )

                # Link Post and MediaAsset
                attachment = PostAttachment(
                    post_id=post.id,
                    media_asset_id=media_asset.id
                )
                db.add(attachment)

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


async def update_post_service(
    post_id: UUID,
    user_id: UUID,
    payload: UpdatePostRequest,
    force_draft: bool,
    db: AsyncSession
) -> Post:
    """
    Update an existing post.
    Validates ownership, updates caption, text, visibility, and attachments.
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

    # Mandatory caption update
    post.caption = payload.caption

    # Optional text update
    if payload.text is not None:
        post.content_html = payload.text

    # Visibility / state updates
    if force_draft:
        post.state = PostState.hidden if payload.visibility == "hidden" else PostState.draft
    else:
        if payload.visibility == "hidden":
            post.state = PostState.hidden
        elif payload.visibility == "public" and post.state == PostState.hidden:
            post.state = PostState.draft

    try:
        # Manage media attachments if passed
        if payload.media is not None:
            # Delete existing attachments
            await db.execute(
                text("DELETE FROM post_attachments WHERE post_id = :post_id").bindparams(post_id=post.id)
            )

            # Create new ones
            for media_item in payload.media:
                res_media = await db.execute(select(MediaAsset).where(MediaAsset.id == media_item.id))
                media_asset = res_media.scalar_one_or_none()
                if not media_asset:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Media asset with ID {media_item.id} not found"
                    )
                if media_asset.owner_user_id != user_id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Media asset with ID {media_item.id} does not belong to the authenticated user"
                    )

                attachment = PostAttachment(
                    post_id=post.id,
                    media_asset_id=media_asset.id
                )
                db.add(attachment)

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


async def publish_post_service(
    post_id: UUID,
    user_id: UUID,
    db: AsyncSession
) -> Post:
    """
    Publish a post, transitioning state to processing.
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

    post.state = PostState.processing

    try:
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


async def get_feed_service(
    current_user_id: UUID,
    db: AsyncSession
) -> list[Post]:
    """
    Get public feed posts ordered by latest first.
    """
    # Shows published posts from all users
    stmt = select(Post).where(Post.state == PostState.published).order_by(Post.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())
