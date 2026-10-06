from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic_settings import BaseSettings
from pydantic import Field


class ImageStorageSettings(BaseSettings):
    S3_BUCKET: str | None = Field(default=None, alias="S3_BUCKET")
    S3_ACCESS_KEY: str | None = Field(default=None, alias="S3_ACCESS_KEY")
    S3_SECRET_KEY: str | None = Field(default=None, alias="S3_SECRET_KEY")
    S3_ENDPOINT: str | None = Field(default=None, alias="S3_ENDPOINT")
    S3_FILE_ENDPOINT: str | None = Field(default=None, alias="S3_FILE_ENDPOINT")
    S3_CDN_ENDPOINT: str | None = Field(default=None, alias="S3_CDN_ENDPOINT")
    base_url: str | None = Field(default=None, alias="BASE_URL")
    base_url_img: str | None = Field(default=None, alias="BASE_URL_IMG")

    # Legacy fallback fields
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_s3_bucket: str | None = Field(default=None, alias="AWS_S3_BUCKET")

    class Config:
        env_file = ".env"
        extra = "ignore"
        populate_by_name = True

    @property
    def effective_bucket(self) -> str | None:
        return self.S3_BUCKET or self.aws_s3_bucket

    @property
    def effective_access_key(self) -> str | None:
        return self.S3_ACCESS_KEY or self.aws_access_key_id

    @property
    def effective_secret_key(self) -> str | None:
        return self.S3_SECRET_KEY or self.aws_secret_access_key

    @property
    def public_media_endpoint(self) -> str:
        """CDN host for public media URLs; falls back to S3_FILE_ENDPOINT."""
        return (self.S3_CDN_ENDPOINT or self.S3_FILE_ENDPOINT or "").rstrip("/")


settings = ImageStorageSettings()


def force_https_url(url: str | None) -> str:
    """Upgrade a URL or host to HTTPS.

    Relative paths (``/static/...``) are left unchanged. Used so Spaces
    clients and download links never emit ``http://``, which Chrome blocks
    for ZIP and mixed-content image loads.
    """
    if not url:
        return ""
    raw = str(url).strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/"):
        return raw
    if raw.startswith("http://"):
        return "https://" + raw[len("http://") :]
    if "://" not in raw:
        return f"https://{raw}"
    return raw


def create_s3_client(s_settings: ImageStorageSettings = settings):
    kwargs: dict[str, Any] = {
        "service_name": "s3",
        "aws_access_key_id": s_settings.effective_access_key,
        "aws_secret_access_key": s_settings.effective_secret_key,
        "config": Config(signature_version="s3v4"),
    }
    if s_settings.S3_ENDPOINT:
        kwargs["endpoint_url"] = force_https_url(s_settings.S3_ENDPOINT)
    elif s_settings.aws_region:
        kwargs["region_name"] = s_settings.aws_region
    return boto3.client(**kwargs)


# Initialize boto3 client
s3_client = create_s3_client(settings)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def normalize_object_content_type(
    content_type: str | None,
    content: bytes | None = None,
) -> str:
    """Return a CDN-safe object ContentType. Never sets ContentEncoding.

    Strips ``; charset=...``. If the client MIME is missing or generic,
    sniff PNG/JPEG magic bytes so PNGs are stored as ``image/png``.
    """
    raw = (content_type or "").split(";")[0].strip().lower()
    if raw == "image/jpg":
        raw = "image/jpeg"
    if raw in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        return raw
    if content:
        if content.startswith(_PNG_MAGIC):
            return "image/png"
        if content.startswith(_JPEG_MAGIC):
            return "image/jpeg"
        if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
            return "image/gif"
        if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
            return "image/webp"
    return raw or "application/octet-stream"


def save_image(file_name: str, content: bytes, content_type: str = "image/png") -> None:
    if settings.effective_bucket:
        s3_client.put_object(
            Bucket=settings.effective_bucket,
            Key=file_name,
            Body=content,
            ContentType=normalize_object_content_type(content_type, content),
            ACL="public-read",
        )
    else:
        # Save locally
        from pathlib import Path
        base_static_dir = Path(__file__).resolve().parents[2] / "entrypoints" / "static" / "uploads"
        target_path = base_static_dir / file_name
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)


def build_image_key(image_name: str, prefix: str = "images") -> str:
    """Builds a unique storage key for an image."""
    return f"{prefix}/{image_name}"


def normalize_image_name(image_name: str) -> str:
    """Normalizes the image name, e.g. stripping spaces or returning it as is."""
    if not image_name:
        return ""
    return image_name.strip()


def generate_upload_url(file_name: str, expiration: int = 3600) -> str:
    """Generate a presigned URL to upload a file to S3."""
    if not settings.effective_bucket:
        return ""
    try:
        response = s3_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": settings.effective_bucket, "Key": file_name},
            ExpiresIn=expiration
        )
        return response
    except ClientError as e:
        print(f"Error generating S3 upload URL: {e}")
        return ""


def public_media_url(file_name: str) -> str:
    """Build a public CDN/file URL for a storage key.

    Returns an empty string when no public endpoint is configured.
    Absolute http(s) URLs are returned unchanged.
    """
    if not file_name:
        return ""
    if file_name.startswith("http://") or file_name.startswith("https://"):
        return file_name
    endpoint = settings.public_media_endpoint
    if not endpoint:
        return ""
    return f"{endpoint}/{file_name.lstrip('/')}"


def origin_media_url(file_name: str) -> str:
    """Build a virtual-hosted HTTPS origin Spaces URL for a storage key.

    Used when a caller needs the origin host rather than the CDN.
    Returns empty when origin is not configured.
    """
    if not file_name:
        return ""
    if file_name.startswith("http://") or file_name.startswith("https://"):
        return force_https_url(file_name)
    bucket = settings.effective_bucket
    endpoint = (settings.S3_ENDPOINT or "").strip().rstrip("/")
    if not bucket or not endpoint:
        return ""
    raw = force_https_url(endpoint)
    parsed = urlparse(raw)
    host = parsed.netloc or parsed.path.split("/")[0]
    bucket_prefix = f"{bucket}."
    if host.startswith(bucket_prefix):
        host = host[len(bucket_prefix) :]
    if not host:
        return ""
    return f"https://{bucket}.{host}/{file_name.lstrip('/')}"


def generate_profile_image_url(file_name: str, expiration: int = 3600) -> str:
    """Generate a download URL for profile or banner photos.

    Uses the public CDN (``S3_CDN_ENDPOINT``), same as post media.
    Data-export downloads remain on origin ``S3_ENDPOINT``.
    """
    return generate_download_url(file_name, expiration)


def generate_download_url(file_name: str, expiration: int = 3600) -> str:
    """Return a public CDN URL for media, or a local/presigned fallback."""
    if not file_name:
        return ""
    # If it is a full HTTP URL or static path already, return it
    if file_name.startswith("http://") or file_name.startswith("https://"):
        return file_name
    public_url = public_media_url(file_name)
    if public_url:
        return public_url
    if settings.effective_bucket:
        try:
            response = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.effective_bucket, "Key": file_name},
                ExpiresIn=expiration
            )
            return response
        except ClientError as e:
            print(f"Error generating S3 download URL: {e}")
            return ""
    if settings.base_url_img:
        endpoint = settings.base_url_img.rstrip("/")
        clean_key = file_name.lstrip("/")
        if not clean_key.startswith("static/uploads/"):
            clean_key = f"static/uploads/{clean_key}"
        return f"{endpoint}/{clean_key}"
    if settings.base_url:
        endpoint = settings.base_url.rstrip("/")
        clean_path = file_name.lstrip("/")
        if not clean_path.startswith("static/uploads/"):
            clean_path = f"static/uploads/{clean_path}"
        return f"{endpoint}/{clean_path}"
    if file_name.startswith("/static/"):
        return file_name
    return f"/static/uploads/{file_name}"


def delete_file(file_name: str) -> None:
    """Delete a file from S3 or local directory."""
    if not file_name:
        return
    if not settings.effective_bucket:
        from pathlib import Path
        base_static_dir = Path(__file__).resolve().parents[2] / "entrypoints" / "static" / "uploads"
        target_path = base_static_dir / file_name
        try:
            if target_path.exists():
                target_path.unlink()
        except Exception as e:
            print(f"Error deleting local file: {e}")
        return
    try:
        s3_client.delete_object(Bucket=settings.effective_bucket, Key=file_name)
    except ClientError as e:
        print(f"Error deleting file from S3: {e}")


def file_exists(file_name: str) -> bool:
    """Check if a file exists in S3 or local directory."""
    if not file_name:
        return False
    if not settings.effective_bucket:
        from pathlib import Path
        base_static_dir = Path(__file__).resolve().parents[2] / "entrypoints" / "static" / "uploads"
        target_path = base_static_dir / file_name
        return target_path.exists()
    try:
        s3_client.head_object(Bucket=settings.effective_bucket, Key=file_name)
        return True
    except ClientError as e:
        # Check if error is 404 Not Found
        if e.response.get("Error", {}).get("Code") == "404":
            return False
        # Log other exceptions
        print(f"Error checking file existence in S3: {e}")
        return False


async def upload_image_to_s3(image_data: str, prefix: str = "profiles") -> str:
    """
    If image_data is a remote URL, downloads and uploads it to S3.
    If image_data is a base64 data URL, decodes and uploads it to S3.
    Otherwise, returns the normalized string.
    Returns the generated download URL or the original URL.
    """
    import httpx
    import base64
    import uuid

    if not image_data:
        return ""

    normalized = image_data.strip() if hasattr(image_data, 'strip') else str(image_data).strip()
    # If it's already an S3 URL of our bucket, return it as is
    if settings.aws_s3_bucket and settings.aws_s3_bucket in normalized:
        return normalized

    # 1. Handle HTTP/HTTPS URL
    if normalized.startswith("http://") or normalized.startswith("https://"):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(normalized, timeout=10.0)
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "image/jpeg")
                    ext = content_type.split("/")[-1] if "/" in content_type else "jpg"
                    ext = ext.split(";")[0].split("?")[0]
                    file_name = f"{prefix}/{uuid.uuid4()}.{ext}"
                    
                    save_image(file_name, response.content, content_type)
                    return generate_download_url(file_name)
        except Exception as e:
            print(f"Error downloading/uploading image URL: {e}")
            return normalized

    # 2. Handle Base64 data URI
    if normalized.startswith("data:image/"):
        try:
            header, encoded = normalized.split(",", 1)
            content_type = header.split(";")[0].split(":")[1]
            ext = content_type.split("/")[-1] if "/" in content_type else "png"
            file_bytes = base64.b64decode(encoded, validate=True)
            file_name = f"{prefix}/{uuid.uuid4()}.{ext}"
            
            save_image(file_name, file_bytes, content_type)
            return generate_download_url(file_name)
        except Exception as e:
            print(f"Error decoding/uploading base64 image: {e}")
            return normalized

    # Otherwise, return it as is
    return normalized


def _image_extension(content_type: str, default: str = "jpg") -> str:
    raw = (content_type or "").split(";")[0].split("/")[-1].strip().lower()
    if raw in {"jpeg", "jpg", "png", "webp", "gif"}:
        return "jpg" if raw == "jpeg" else raw
    return default


async def copy_remote_image_to_s3(image_data: str, prefix: str = "profiles") -> str:
    """Download a remote or base64 image and store it.

    Returns the storage key (e.g. ``profiles/<uuid>.jpg``) on success, or ``""``.
    """
    import httpx
    import base64
    import uuid

    if not image_data:
        return ""

    normalized = str(image_data).strip()
    if not normalized:
        return ""

    if normalized.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(normalized, timeout=10.0)
            if response.status_code != 200:
                return ""
            content_type = response.headers.get("content-type", "image/jpeg")
            ext = _image_extension(content_type)
            file_name = f"{prefix}/{uuid.uuid4()}.{ext}"
            save_image(file_name, response.content, content_type.split(";")[0].strip() or "image/jpeg")
            return file_name
        except Exception as e:
            print(f"Error downloading/uploading image URL: {e}")
            return ""

    if normalized.startswith("data:image/"):
        try:
            header, encoded = normalized.split(",", 1)
            content_type = header.split(";")[0].split(":")[1]
            ext = _image_extension(content_type, default="png")
            file_bytes = base64.b64decode(encoded, validate=True)
            file_name = f"{prefix}/{uuid.uuid4()}.{ext}"
            save_image(file_name, file_bytes, content_type)
            return file_name
        except Exception as e:
            print(f"Error decoding/uploading base64 image: {e}")
            return ""

    return ""