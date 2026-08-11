from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from uuid import UUID

from botocore.exceptions import ClientError
from fastapi import HTTPException, UploadFile, status

from core.images import config as image_config

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_CAMPAIGN = 5
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "text/plain",
}
STORAGE_PREFIX = "email-campaigns"


def _normalize_key(storage_key: str) -> str:
    clean = (storage_key or "").replace("\\", "/").lstrip("/")
    if not clean or ".." in clean.split("/"):
        raise ValueError("Invalid storage key")
    return clean


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "attachment.bin").name.strip() or "attachment.bin"
    name = re.sub(r"[^\w.\-()+ ]+", "_", name)
    return name[:200]


class BulkSendStorage:
    """Private Spaces storage for bulk-email campaign attachments."""

    def __init__(self) -> None:
        self._settings = image_config.settings
        self._client = image_config.s3_client

    @property
    def bucket(self) -> str:
        bucket = self._settings.effective_bucket
        if not bucket:
            raise RuntimeError("S3_BUCKET is not configured; cannot store bulk email attachments.")
        return bucket

    def build_tmp_key(self, admin_id: UUID, filename: str) -> str:
        return f"{STORAGE_PREFIX}/tmp/{admin_id}/{uuid.uuid4()}/{_safe_filename(filename)}"

    def upload(self, storage_key: str, data: bytes, content_type: str) -> str:
        key = _normalize_key(storage_key)
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ContentDisposition=f'attachment; filename="{Path(key).name}"',
        )
        logger.info("Uploaded bulk email attachment key=%s bytes=%d", key, len(data))
        return key

    def exists(self, storage_key: str) -> bool:
        key = _normalize_key(storage_key)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            logger.exception("head_object failed for bulk attachment key=%s", key)
            return False
        except Exception:
            logger.exception("Unexpected error checking bulk attachment key=%s", key)
            return False

    def download(self, storage_key: str) -> bytes:
        key = _normalize_key(storage_key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            logger.exception("Failed downloading bulk attachment key=%s", key)
            raise RuntimeError(f"Could not download attachment: {key}") from exc

    def is_allowed_admin_key(self, storage_key: str, admin_id: UUID) -> bool:
        key = _normalize_key(storage_key)
        return key.startswith(f"{STORAGE_PREFIX}/tmp/{admin_id}/") or key.startswith(
            f"{STORAGE_PREFIX}/"
        )


def get_bulk_send_storage() -> BulkSendStorage:
    return BulkSendStorage()


async def upload_attachment_file(
    *,
    file: UploadFile,
    admin_id: UUID,
    storage: BulkSendStorage | None = None,
) -> dict[str, str]:
    storage = storage or get_bulk_send_storage()
    content_type = (file.content_type or "").lower().strip()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {content_type or 'unknown'}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds maximum allowed limit of 10 MB",
        )

    file_name = _safe_filename(file.filename)
    storage_key = storage.build_tmp_key(admin_id, file_name)
    storage.upload(storage_key, data, content_type)
    return {
        "file_name": file_name,
        "storage_key": storage_key,
        "content_type": content_type,
    }
