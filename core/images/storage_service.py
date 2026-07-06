from __future__ import annotations

from typing import Any
from fastapi import HTTPException, status, UploadFile
from botocore.exceptions import ClientError

from core.images import config

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
}

MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def get_media_url(key: str) -> str:
    """
    Returns full CDN URL for a given storage key handling trailing slashes safely.
    Format: f"{settings.S3_CDN_ENDPOINT}/{key}"
    """
    if not key:
        return ""
    if key.startswith("http://") or key.startswith("https://"):
        return key
    
    clean_key = key.lstrip("/")
    image_endpoint = (config.settings.S3_FILE_ENDPOINT
            or config.settings.S3_CDN_ENDPOINT or "").rstrip("/")
    if image_endpoint:
        return f"{image_endpoint}/{clean_key}"
    return f"/{clean_key}"


def delete_file(key: str) -> None:
    """
    Deletes object from DigitalOcean Spaces / S3 bucket or local storage.
    """
    if not key:
        return
    bucket = config.settings.effective_bucket
    if not bucket:
        # Local storage fallback
        from pathlib import Path
        base_static_dir = Path(__file__).resolve().parents[2] / "entrypoints" / "static" / "uploads"
        target_path = base_static_dir / key
        try:
            if target_path.exists():
                target_path.unlink()
        except Exception as e:
            print(f"Error deleting local file: {e}")
        return

    try:
        config.s3_client.delete_object(Bucket=bucket, Key=key)
    except ClientError as e:
        print(f"Error deleting file from S3: {e}")


def validate_image(content: bytes, content_type: str | None) -> None:
    """
    Validates image file existence, MIME type, and maximum file size (5MB).
    Raises HTTPException status 400 on error.
    """
    if not content or len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty or does not exist",
        )

    if not content_type or content_type.lower() not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Allowed types are: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}",
        )

    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds maximum allowed limit of 5 MB",
        )


def get_extension(content_type: str, filename: str | None = None) -> str:
    """Extracts clean file extension from MIME type or filename."""
    ct = (content_type or "").lower()
    if ct in MIME_TO_EXT:
        return MIME_TO_EXT[ct]
    if filename and "." in filename:
        ext = filename.split(".")[-1].lower()
        if ext in {"jpg", "jpeg", "png", "webp", "gif"}:
            return "jpg" if ext == "jpeg" else ext
    return "png"


class StorageService:
    """
    Reusable Storage Service for DigitalOcean Spaces (S3-compatible).
    Encapsulates upload, delete, and URL generation logic for images and future media.
    """

    @staticmethod
    def get_media_url(key: str) -> str:
        return get_media_url(key)

    @staticmethod
    def delete_file(key: str) -> None:
        return delete_file(key)

    @staticmethod
    def upload_file(content: bytes, key: str, content_type: str) -> str:
        """
        Low-level upload primitive to store file in S3 / DigitalOcean Spaces.
        """
        bucket = config.settings.effective_bucket
        if bucket:
            response= config.s3_client.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                ACL="public-read",
            )
               
        else:
            config.save_image(file_name=key, content=content, content_type=content_type)
        return key

    @classmethod
    async def _extract_content(
        cls, file_input
    ) -> tuple[bytes, str | None, str | None]:

        if hasattr(file_input, "read"):
            content = await file_input.read()
            content_type = getattr(file_input, "content_type", None)
            filename = getattr(file_input, "filename", None)

        elif isinstance(file_input, bytes):
            content = file_input
            content_type = None
            filename = None

        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file input provided. Received: {type(file_input)}",
            )

        return content, content_type, filename

    @classmethod
    async def upload_banner(
        cls,
        file: UploadFile | bytes,
        banner_id: str | int,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """
        Uploads banner image under banners/{banner_id}.{ext}.
        Replaces existing file if present.
        """
        content, extracted_ct, extracted_fn = await cls._extract_content(file)
        final_ct = content_type or extracted_ct or "image/png"
        final_fn = filename or extracted_fn

        validate_image(content, final_ct)

        ext = get_extension(final_ct, final_fn)
        key = f"banners/{banner_id}.{ext}"

        cls.upload_file(content=content, key=key, content_type=final_ct)
        url = get_media_url(key)

        return {
            "status": True,
            "message": "image uploaded",
            "data": {
                "key": key,
                "url": url,
            },
        }

    @classmethod
    async def upload_profile(
        cls,
        file: UploadFile | bytes,
        user_id: str | int,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """
        Uploads profile image under profiles/{user_id}.{ext}.
        Replaces existing file if present.
        """
        content, extracted_ct, extracted_fn = await cls._extract_content(file)
        final_ct = content_type or extracted_ct or "image/png"
        final_fn = filename or extracted_fn

        validate_image(content, final_ct)

        ext = get_extension(final_ct, final_fn)
        key = f"profiles/{user_id}.{ext}"

        cls.upload_file(content=content, key=key, content_type=final_ct)
        url = get_media_url(key)

        return {
            "status": True,
            "message": "image uploaded",
            "data": {
                "key": key,
                "url": url,
            },
        }

    @classmethod
    async def upload_post_media(
        cls,
        file: UploadFile | bytes,
        file_uuid: str | UUID,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """
        Uploads post media (image, video, document, gif) under posts/{file_uuid}.{ext}.
        """
        content, extracted_ct, extracted_fn = await cls._extract_content(file)

        print("FILENAME:", extracted_fn)
        print("CONTENT TYPE:", extracted_ct)
        print("SIZE:", len(content))
        print("FIRST 20 BYTES:", content[:20])
        final_ct = content_type or extracted_ct or "application/octet-stream"
        final_fn = filename or extracted_fn

        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File size exceeds maximum allowed limit of 5 MB",
            )

        from pathlib import Path
        ext = Path(final_fn).suffix if final_fn else ""
        if not ext:
            if "jpeg" in final_ct or "jpg" in final_ct:
                ext = ".jpg"
            elif "png" in final_ct:
                ext = ".png"
            elif "gif" in final_ct:
                ext = ".gif"
            elif "mp4" in final_ct:
                ext = ".mp4"
            elif "pdf" in final_ct:
                ext = ".pdf"
            else:
                ext = ".bin"
        
        ext = ext.lstrip(".")
        key = f"posts/{file_uuid}.{ext}"

        cls.upload_file(content=content, key=key, content_type=final_ct)
        url = get_media_url(key)

        return {
            "status": True,
            "message": "media uploaded",
            "data": {
                "key": key,
                "url": url,
            },
        }


# Export service module instance & functions
storage_service = StorageService()
