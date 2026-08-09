from __future__ import annotations

import logging
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from botocore.exceptions import ClientError

from apps.export.config import settings as export_settings
from core.images import config as image_config

logger = logging.getLogger(__name__)


class ExportStorage(ABC):
    """Abstract storage for generated export ZIP archives."""

    @abstractmethod
    def upload(self, storage_key: str, data: bytes, content_type: str = "application/zip") -> str:
        """Upload bytes and return the storage_key used."""

    @abstractmethod
    def exists(self, storage_key: str) -> bool:
        ...

    @abstractmethod
    def generate_download_url(self, storage_key: str, expires_in: int | None = None) -> str:
        """Return a short-lived signed download URL (never persist this)."""

    @abstractmethod
    def delete(self, storage_key: str) -> None:
        ...


class DigitalOceanSpacesExportStorage(ExportStorage):
    """Store private export ZIPs in the existing DigitalOcean Spaces bucket.

    Reuses ``core.images.config.s3_client`` and ImageStorageSettings.
    Does not apply public ACLs and does not expose CDN/file endpoints.
    """

    def __init__(self) -> None:
        self._settings = image_config.settings
        self._client = image_config.s3_client

    @property
    def bucket(self) -> str:
        bucket = self._settings.effective_bucket
        if not bucket:
            raise RuntimeError(
                "S3_BUCKET is not configured; cannot store data exports in Spaces."
            )
        return bucket

    def upload(
        self,
        storage_key: str,
        data: bytes,
        content_type: str = "application/zip",
    ) -> str:
        key = _normalize_key(storage_key)
        # Intentionally omit ACL so objects remain private (no public-read).
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ContentDisposition=f'attachment; filename="{Path(key).name}"',
        )
        logger.info(
            "Uploaded export archive to Spaces key=%s bytes=%d",
            key,
            len(data),
        )
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
            logger.exception("head_object failed for export key=%s", key)
            return False
        except Exception:
            logger.exception("Unexpected error checking export key=%s", key)
            return False

    def generate_download_url(
        self,
        storage_key: str,
        expires_in: int | None = None,
    ) -> str:
        key = _normalize_key(storage_key)
        ttl = expires_in or export_settings.export_signed_url_expires_seconds
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ResponseContentDisposition": f'attachment; filename="{Path(key).name}"',
                },
                ExpiresIn=ttl,
            )
        except ClientError as exc:
            logger.exception("Failed generating signed URL for export key=%s", key)
            raise RuntimeError("Could not generate export download URL") from exc

    def delete(self, storage_key: str) -> None:
        key = _normalize_key(storage_key)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
            logger.info("Deleted export archive from Spaces key=%s", key)
        except ClientError:
            # Missing objects are treated as already cleaned up.
            logger.warning("Failed deleting export key=%s (may already be gone)", key, exc_info=True)


def _normalize_key(storage_key: str) -> str:
    clean = (storage_key or "").replace("\\", "/").lstrip("/")
    if not clean or ".." in clean.split("/"):
        raise ValueError("Invalid storage key")
    return clean


def write_temp_zip(data: bytes) -> Path:
    """Write ZIP bytes to a temporary local file; caller must delete it."""
    fd, name = tempfile.mkstemp(prefix="kampulynk_export_", suffix=".zip")
    path = Path(name)
    try:
        with open(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def get_export_storage() -> ExportStorage:
    """Production export storage is DigitalOcean Spaces."""
    return DigitalOceanSpacesExportStorage()
