from __future__ import annotations

import logging
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from core.images import config as image_config
from core.images.config import force_https_url

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
    def generate_download_url(self, storage_key: str, expires_in: int) -> str:
        """Return a presigned download URL valid for ``expires_in`` seconds.

        ``expires_in`` is required — callers must pass the desired TTL
        explicitly (e.g. 604800 for 7 days). The URL must never be persisted.
        """

    @abstractmethod
    def delete(self, storage_key: str) -> None:
        ...


class DigitalOceanSpacesExportStorage(ExportStorage):
    """Store private export ZIPs in the existing DigitalOcean Spaces bucket.

    Upload/delete use the origin ``S3_ENDPOINT`` client (Spaces API).
    Download links are also signed against origin ``S3_ENDPOINT`` — never the
    CDN — with virtual-hosted HTTPS so SigV4 matches and Chrome will not block
    the ZIP as an insecure download. Objects remain private (no public-read ACL).
    """

    def __init__(self) -> None:
        self._settings = image_config.settings
        self._client = image_config.s3_client
        self._origin_presign_client = None

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
        expires_in: int,
    ) -> str:
        """Generate a presigned origin Spaces download URL.

        Signed against ``S3_ENDPOINT`` (not ``S3_CDN_ENDPOINT``). Spaces CDN
        does not validate SigV4 the same way as origin, which produces
        ``SignatureDoesNotMatch``. ``expires_in`` is the TTL in seconds
        (e.g. 604800 = 7 days). Never persist the URL.
        """
        key = _normalize_key(storage_key)
        try:
            url = self._presign_client().generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ResponseContentDisposition": f'attachment; filename="{Path(key).name}"',
                },
                ExpiresIn=expires_in,
            )
            return force_https_url(url)
        except ClientError as exc:
            logger.exception("Failed generating signed URL for export key=%s", key)
            raise RuntimeError("Could not generate export download URL") from exc

    def _presign_client(self):
        """Return a virtual-hosted origin client for signing download URLs."""
        if self._origin_presign_client is not None:
            return self._origin_presign_client
        endpoint = getattr(self._settings, "S3_ENDPOINT", None) or ""
        if not isinstance(endpoint, str) or not endpoint.strip():
            return self._client
        self._origin_presign_client = _create_origin_presign_client(
            self._settings,
            self.bucket,
        )
        return self._origin_presign_client

    def delete(self, storage_key: str) -> None:
        key = _normalize_key(storage_key)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
            logger.info("Deleted export archive from Spaces key=%s", key)
        except ClientError:
            # Missing objects are treated as already cleaned up.
            logger.warning("Failed deleting export key=%s (may already be gone)", key, exc_info=True)


def _origin_endpoint(settings: object) -> str:
    endpoint = getattr(settings, "S3_ENDPOINT", None) or ""
    if not isinstance(endpoint, str):
        return ""
    return endpoint.strip().rstrip("/")


def _boto_endpoint_url_for_origin(s3_endpoint: str, bucket: str) -> str:
    """Derive the boto3 ``endpoint_url`` for virtual-hosted origin signing.

    ``S3_ENDPOINT`` is typically ``https://{region}.digitaloceanspaces.com``
    or ``https://{bucket}.{region}.digitaloceanspaces.com``. boto3 virtual
    addressing wants the region host so the signed URL becomes
    ``https://{bucket}.{region}.digitaloceanspaces.com/{key}``.
    """
    raw = s3_endpoint if "://" in s3_endpoint else f"https://{s3_endpoint}"
    parsed = urlparse(raw)
    host = parsed.netloc or parsed.path.split("/")[0]
    bucket_prefix = f"{bucket}."
    if host.startswith(bucket_prefix):
        host = host[len(bucket_prefix) :]
    return force_https_url(f"https://{host}")


def _create_origin_presign_client(settings: object, bucket: str):
    """boto3 client pointed at origin Spaces for presigning download URLs."""
    endpoint_url = _boto_endpoint_url_for_origin(_origin_endpoint(settings), bucket)
    return boto3.client(
        "s3",
        aws_access_key_id=getattr(settings, "effective_access_key", None),
        aws_secret_access_key=getattr(settings, "effective_secret_key", None),
        endpoint_url=endpoint_url,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )


def _ensure_https(url: str) -> str:
    """Force HTTPS so Chrome will not block the ZIP as an insecure download."""
    return force_https_url(url)


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
