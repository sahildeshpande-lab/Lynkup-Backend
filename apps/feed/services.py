from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
from fastapi import UploadFile, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from common.enums import MediaType, MediaAssetState, PostState
from apps.accounts.db_models import User
from apps.feed.db_models import Post, MediaAsset, PostAttachment, Hashtag, PostHashtag, PostRevision
from apps.feed.schemas import SavePostRequest, EditPostRequest
from apps.feed.content_utils import (
    sanitize_html,
    extract_hashtags,
    validate_content,
    validate_media_count,
    validate_media_asset,
)
from core.images import save_image, generate_download_url


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Response formatting
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Media upload (unchanged)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_content_dict(payload_content) -> dict:
    """
    Build the content JSONB dict from a PostContentPayload,
    sanitizing HTML before storage.
    """
    content_dict: dict = {
        "caption": payload_content.caption,
        "visibility": payload_content.visibility,
    }
    if payload_content.content_html is not None:
        content_dict["content_html"] = sanitize_html(payload_content.content_html)
    return content_dict


async def _build_media_snapshot(
    post_id: UUID,
    db: AsyncSession,
) -> list[dict]:
    """
    Build a JSON-serialisable snapshot of the post's current media attachments
    for the revision audit trail.
    """
    stmt = (
        select(PostAttachment, MediaAsset)
        .join(MediaAsset, PostAttachment.media_asset_id == MediaAsset.id)
        .where(PostAttachment.post_id == post_id)
    )

    result = await db.execute(stmt)

    snapshot = []
    for attachment, asset in result.all():
        snapshot.append({
            "id": str(asset.id),
            "key": asset.key,
            "type": asset.type.value if hasattr(asset.type, "value") else str(asset.type),
            "original_filename": asset.original_filename,
            "mime_type": asset.mime_type,
            "file_size": asset.file_size,
        })

    return snapshot


async def _create_revision(
    post: Post,
    editor_user_id: UUID,
    db: AsyncSession,
) -> None:
    """
    Create an immutable PostRevision audit record capturing the current
    state of the post's content and media.
    """
    media_snapshot = await _build_media_snapshot(post.id, db)

    revision = PostRevision(
        post_id=post.id,
        editor_user_id=editor_user_id,
        content=post.content,
        media=media_snapshot,
    )

    db.add(revision)


async def _sync_hashtags(
    post_id: UUID,
    content_html: str | None,
    db: AsyncSession,
) -> None:
    """
    Extract hashtags from ``content_html`` and synchronise the
    ``PostHashtag`` join table.

    - New hashtags are inserted into the ``hashtags`` table (get-or-create).
    - All existing ``PostHashtag`` rows for this post are replaced.
    """
    # Delete existing mappings
    await db.execute(
        text("DELETE FROM post_hashtags WHERE post_id = :post_id").bindparams(post_id=post_id)
    )

    if not content_html:
        return

    tags = extract_hashtags(content_html)
    if not tags:
        return

    for tag_text in tags:
        # Get or create the Hashtag record
        result = await db.execute(select(Hashtag).where(Hashtag.tag == tag_text))
        hashtag = result.scalar_one_or_none()
        if not hashtag:
            hashtag = Hashtag(tag=tag_text)
            db.add(hashtag)
            await db.flush()

        post_hashtag = PostHashtag(
            post_id=post_id,
            hashtag_id=hashtag.id,
        )
        db.add(post_hashtag)


async def _verify_and_attach_media(
    post_id: UUID,
    user_id: UUID,
    media_items: list,
    db: AsyncSession,
    replace: bool = False,
) -> None:
    """
    Verify ownership of each media asset and create PostAttachment records.

    If ``replace`` is True, existing attachments for the post are deleted first.
    """
    if replace:
        await db.execute(
            text("DELETE FROM post_attachments WHERE post_id = :post_id").bindparams(post_id=post_id)
        )

    for media_item in media_items:
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

        # Validate attachment rules (document/audio size and type)
        media_type_str = media_item.type.value if hasattr(media_item.type, "value") else str(media_item.type)
        try:
            validate_media_asset(media_asset, media_type_str)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc)
            )

        attachment = PostAttachment(
            post_id=post_id,
            media_asset_id=media_asset.id
        )
        db.add(attachment)


# ---------------------------------------------------------------------------
# Save post (unified create / update draft)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Edit post (PATCH /posts/)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Publish post
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Get post
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Delete post
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# List user posts
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------

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
