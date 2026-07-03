from __future__ import annotations

import os
from typing import Any
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic_settings import BaseSettings
from pydantic import Field


class ImageStorageSettings(BaseSettings):
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_s3_bucket: str | None = Field(default=None, alias="AWS_S3_BUCKET")
    base_url: str | None = Field(default=None, alias="BASE_URL")
    base_url_img: str | None = Field(default=None, alias="BASE_URL_Img")

    class Config:
        env_file = ".env"
        extra = "ignore"
        populate_by_name = True


settings = ImageStorageSettings()

# Initialize boto3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=settings.aws_access_key_id,
    aws_secret_access_key=settings.aws_secret_access_key,
    region_name=settings.aws_region,
    config=Config(signature_version="s3v4")
)


def save_image(file_name: str, content: bytes, content_type: str = "image/png") -> None:
    if settings.aws_s3_bucket:
        s3_client.put_object(
            Bucket=settings.aws_s3_bucket,
            Key=file_name,
            Body=content,
            ContentType=content_type
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
    if not settings.aws_s3_bucket:
        return ""
    try:
        response = s3_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": settings.aws_s3_bucket, "Key": file_name},
            ExpiresIn=expiration
        )
        return response
    except ClientError as e:
        print(f"Error generating S3 upload URL: {e}")
        return ""


def _public_base_url() -> str | None:
    base = settings.base_url or settings.base_url_img
    if not base:
        return None
    return base.rstrip("/")


def apply_base_url_img(url: str) -> str:
    """Prefix relative image paths with BASE_URL (or BASE_URL_Img) when configured."""
    base = _public_base_url()
    if not url or not base:
        return url
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        return f"{base}{url}"
    return f"{base}/{url}"


def generate_profile_image_url(file_name: str, expiration: int = 3600) -> str:
    """Generate a download URL for profile or banner photos with optional base URL."""
    return apply_base_url_img(generate_download_url(file_name, expiration))


def generate_download_url(file_name: str, expiration: int = 3600) -> str:
    """Generate a presigned URL to download a file from S3 or return local path."""
    if not file_name:
        return ""
    # If it is a full HTTP URL already, return it
    if file_name.startswith(("http://", "https://")):
        return file_name
    if file_name.startswith("/static/"):
        return apply_base_url_img(file_name)
    if not settings.aws_s3_bucket:
        return apply_base_url_img(f"/static/uploads/{file_name}")
    try:
        response = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.aws_s3_bucket, "Key": file_name},
            ExpiresIn=expiration
        )
        return response
    except ClientError as e:
        print(f"Error generating S3 download URL: {e}")
        return ""


def delete_file(file_name: str) -> None:
    """Delete a file from S3 or local directory."""
    if not file_name:
        return
    if not settings.aws_s3_bucket:
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
        s3_client.delete_object(Bucket=settings.aws_s3_bucket, Key=file_name)
    except ClientError as e:
        print(f"Error deleting file from S3: {e}")


def file_exists(file_name: str) -> bool:
    """Check if a file exists in S3 or local directory."""
    if not file_name:
        return False
    if not settings.aws_s3_bucket:
        from pathlib import Path
        base_static_dir = Path(__file__).resolve().parents[2] / "entrypoints" / "static" / "uploads"
        target_path = base_static_dir / file_name
        return target_path.exists()
    try:
        s3_client.head_object(Bucket=settings.aws_s3_bucket, Key=file_name)
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
            file_bytes = base64.b64decode(encoded)
            file_name = f"{prefix}/{uuid.uuid4()}.{ext}"
            
            save_image(file_name, file_bytes, content_type)
            return generate_download_url(file_name)
        except Exception as e:
            print(f"Error decoding/uploading base64 image: {e}")
            return normalized

    # Otherwise, return it as is
    return normalized

