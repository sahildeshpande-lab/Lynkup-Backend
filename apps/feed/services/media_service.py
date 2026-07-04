from __future__ import annotations

import uuid
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.feed.content_utils import (
    MAX_MEDIA_COUNT,
    validate_media_asset,
)
from apps.feed.db_models import MediaAsset, PostAttachment
from common.enums import MediaAssetState, MediaType
from common.exceptions import ApiError
from core.images import generate_download_url
from core.images.storage_service import storage_service

MAX_IMAGE_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_GIF_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_VIDEO_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_AUDIO_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_DOCUMENT_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_OTHER_UPLOAD_SIZE = 5 * 1024 * 1024

_MAX_UPLOAD_SIZE_BY_TYPE = {
    MediaType.image.value: MAX_IMAGE_UPLOAD_SIZE,
    MediaType.gif.value: MAX_GIF_UPLOAD_SIZE,
    MediaType.video.value: MAX_VIDEO_UPLOAD_SIZE,
    MediaType.audio.value: MAX_AUDIO_UPLOAD_SIZE,
    MediaType.document.value: MAX_DOCUMENT_UPLOAD_SIZE,
    MediaType.other.value: MAX_OTHER_UPLOAD_SIZE,
}

_SUPPORTED_MEDIA_TYPES = {media_type.value for media_type in MediaType}


def get_media_type(content_type: str) -> str:
    """Infer the media type used by feed media assets from a MIME type."""
    normalized = (content_type or "").lower()
    if normalized == "image/gif":
        return MediaType.gif.value
    if normalized.startswith("image/"):
        return MediaType.image.value
    if normalized.startswith("video/"):
        return MediaType.video.value
    if normalized.startswith("audio/"):
        return MediaType.audio.value
    if normalized.startswith("application/") or normalized.startswith("text/"):
        return MediaType.document.value
    return MediaType.other.value


def _validate_upload_file(file: UploadFile, content: bytes, media_type: str) -> None:
    filename = file.filename or "uploaded file"
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{filename} is empty",
        )
    if media_type not in _SUPPORTED_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type for {filename}",
        )

    max_size = _MAX_UPLOAD_SIZE_BY_TYPE[media_type]
    if len(content) > max_size:
        max_size_mb = max_size // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{filename} exceeds maximum upload size of {max_size_mb} MB for {media_type} files",
        )


def _resolve_extension(filename: str | None, content_type: str) -> str:
    ext = Path(filename).suffix if filename else ""
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
    return ext


async def _build_media_asset(
    *,
    user_id: UUID,
    file: UploadFile,
    content: bytes,
) -> MediaAsset:
    content_type = file.content_type or ""
    media_type = MediaType(get_media_type(content_type))

    file_uuid = uuid.uuid4()

    try:
        res = await storage_service.upload_post_media(
            file=content,
            file_uuid=file_uuid,
            content_type=content_type,
            filename=file.filename,
        )
        key = res["data"]["key"]
    except Exception:
        raise ApiError("Storage upload failed")

    return MediaAsset(
        owner_user_id=user_id,
        key=key,
        type=media_type,
        original_filename=file.filename,
        mime_type=content_type,
        file_size=len(content),
        state=MediaAssetState.published,
    )


def _media_asset_to_response(media_asset: MediaAsset) -> dict:
    return {
        "id": media_asset.id,
        "url": generate_download_url(media_asset.key),
        "key": media_asset.key,
        "type": media_asset.type.value if hasattr(media_asset.type, "value") else str(media_asset.type),
    }


async def upload_post_media_service(
    user_id: UUID,
    files: list[UploadFile],
    db: AsyncSession,
) -> list[dict]:
    """
    Validate uploaded files, save them using the storage utility,
    and persist metadata in the MediaAsset table.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one file is required",
        )
    if len(files) > MAX_MEDIA_COUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_MEDIA_COUNT} files allowed per request",
        )

    media_assets: list[MediaAsset] = []
    for file in files:
        content = await file.read()
        media_type = get_media_type(file.content_type or "")
        _validate_upload_file(file, content, media_type)

        media_asset = await _build_media_asset(
            user_id=user_id,
            file=file,
            content=content,
        )
        db.add(media_asset)
        media_assets.append(media_asset)

    try:
        await db.commit()
        for media_asset in media_assets:
            await db.refresh(media_asset)
    except Exception:
        await db.rollback()
        raise ApiError("Database error saving media metadata")

    return [_media_asset_to_response(media_asset) for media_asset in media_assets]


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
