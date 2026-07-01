from __future__ import annotations
import uuid
from pathlib import Path
from uuid import UUID
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from common.enums import MediaType, MediaAssetState
from common.exceptions import ApiError
from apps.feed.db_models import MediaAsset, PostAttachment
from apps.feed.content_utils import validate_media_asset
from core.images import save_image, generate_download_url

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
        raise ApiError("Cannot upload an empty file")

    # Simple content type validation based on MediaType
    content_type = file.content_type or ""
    if media_type == MediaType.image and not content_type.startswith("image/"):
        raise ApiError("Invalid file type for image media asset")
    elif media_type == MediaType.video and not content_type.startswith("video/"):
        raise ApiError("Invalid file type for video media asset")
    elif media_type == MediaType.audio and not content_type.startswith("audio/"):
        raise ApiError("Invalid file type for audio media asset")

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
    except Exception:
        raise ApiError("Storage upload failed")

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
    except Exception:
        await db.rollback()
        raise ApiError("Database error saving media metadata")

    return {
        "id": media_asset.id,
        "key": media_asset.key,
        "type": media_asset.type,
        "url": generate_download_url(key)
    }

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
            raise ApiError(f"Media asset with ID {media_item.id} not found")
        if media_asset.owner_user_id != user_id:
            raise ApiError(
                f"Media asset with ID {media_item.id} does not belong to the authenticated user"
            )

        # Validate attachment rules (document/audio size and type)
        media_type_str = media_item.type.value if hasattr(media_item.type, "value") else str(media_item.type)
        try:
            validate_media_asset(media_asset, media_type_str)
        except ValueError as exc:
            raise ApiError(str(exc))

        attachment = PostAttachment(
            post_id=post_id,
            media_asset_id=media_asset.id
        )
        db.add(attachment)
