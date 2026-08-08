from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from aioapns import APNs, NotificationRequest, PushType
from aioapns.common import PRIORITY_HIGH

from core.apns.config import settings as apns_settings

logger = logging.getLogger(__name__)

# APNs reasons that indicate the device token should be treated as invalid.
_INVALID_TOKEN_REASONS = frozenset(
    {
        "baddevicetoken",
        "unregistered",
        "devicetokennotfortopic",
        "expiredtoken",
    }
)

_apns_client: APNs | None = None
_apns_key_file: Path | None = None


class ApnsDeliveryError(Exception):
    """Raised when APNs rejects or fails to deliver a notification."""

    def __init__(
        self,
        message: str,
        *,
        status: str | None = None,
        reason: str | None = None,
        invalid_token: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.invalid_token = invalid_token


def _is_invalid_token_reason(reason: str | None) -> bool:
    if not reason:
        return False
    return reason.strip().lower() in _INVALID_TOKEN_REASONS


def is_invalid_apns_token_error(exc: BaseException) -> bool:
    if isinstance(exc, ApnsDeliveryError):
        return exc.invalid_token
    message = str(exc).lower()
    return any(token in message for token in _INVALID_TOKEN_REASONS)


def _ensure_apns_key_file() -> Path:
    """
    Materialize ``APNS_PRIVATE_KEY`` into a temporary ``.p8`` file once.

    aioapns token auth consumes key PEM content; we persist it to a temp file
    so the key lives outside process env snapshots and can be cleaned up on
    shutdown. The private key value is never logged.
    """
    global _apns_key_file

    if _apns_key_file is not None and _apns_key_file.is_file():
        return _apns_key_file

    private_key = apns_settings.apns_private_key
    if not private_key:
        raise RuntimeError("APNS_PRIVATE_KEY is not configured")

    fd, raw_path = tempfile.mkstemp(prefix="apns_", suffix=".p8")
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(private_key)
            if not private_key.endswith("\n"):
                handle.write("\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows may not support POSIX mode bits; ignore.
            pass
    except Exception:
        path.unlink(missing_ok=True)
        raise

    _apns_key_file = path
    logger.info("APNs temporary authentication key file created")
    return path


def _load_key_content(key_file: Path) -> str:
    if not key_file.is_file():
        raise FileNotFoundError("APNs temporary key file is missing")
    return key_file.read_text(encoding="utf-8")


def _get_apns_client() -> APNs:
    """Lazy-initialize and cache the APNs client from environment settings."""
    global _apns_client

    if not apns_settings.is_configured:
        raise RuntimeError(
            "APNs is not configured. Set APNS_PRIVATE_KEY, APNS_KEY_ID, "
            "APNS_TEAM_ID, and APNS_TOPIC."
        )

    if _apns_client is not None:
        return _apns_client

    key_file = _ensure_apns_key_file()
    key_content = _load_key_content(key_file)
    _apns_client = APNs(
        key=key_content,
        key_id=apns_settings.apns_key_id,
        team_id=apns_settings.apns_team_id,
        topic=apns_settings.apns_topic,
        use_sandbox=apns_settings.apns_use_sandbox,
    )
    logger.info(
        "APNs client initialized topic=%s sandbox=%s",
        apns_settings.apns_topic,
        apns_settings.apns_use_sandbox,
    )
    return _apns_client


def cleanup_apns_resources() -> None:
    """Drop the cached client and delete the temporary ``.p8`` key file."""
    global _apns_client, _apns_key_file

    _apns_client = None
    key_file = _apns_key_file
    _apns_key_file = None
    if key_file is None:
        return
    try:
        key_file.unlink(missing_ok=True)
        logger.info("APNs temporary authentication key file removed")
    except OSError:
        logger.warning("Failed to remove APNs temporary authentication key file")


def reset_apns_client() -> None:
    """Clear cached APNs state (useful in tests)."""
    cleanup_apns_resources()


def _build_apns_payload(
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an APNs JSON payload with alert, default sound, and custom data."""
    message: dict[str, Any] = {
        "aps": {
            "alert": {
                "title": title,
                "body": body,
            },
            "sound": "default",
        }
    }
    if data:
        for key, value in data.items():
            if key == "aps":
                continue
            message[str(key)] = value
    return message


async def send_apns_notification(
    device_token: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> str:
    """
    Send a single high-priority APNs alert notification.

    Returns the APNs notification id on success.
    Raises ``ApnsDeliveryError`` on rejection (including invalid tokens).
    """
    token = (device_token or "").strip()
    if not token:
        raise ValueError("device_token cannot be blank")

    client = _get_apns_client()
    request = NotificationRequest(
        device_token=token,
        message=_build_apns_payload(title, body, data),
        priority=int(PRIORITY_HIGH),
        push_type=PushType.ALERT,
    )

    try:
        result = await client.send_notification(request)
    except Exception as exc:
        logger.exception(
            "APNs push send failed token=%s error=%s",
            token,
            exc,
        )
        raise

    if result.is_successful:
        logger.info(
            "APNs message sent successfully notification_id=%s token=%s",
            result.notification_id,
            token,
        )
        return result.notification_id

    reason = result.description or "unknown"
    invalid = _is_invalid_token_reason(reason)
    log_fn = logger.warning if invalid else logger.error
    log_fn(
        "APNs delivery failed notification_id=%s token=%s status=%s reason=%s",
        result.notification_id,
        token,
        result.status,
        reason,
    )
    raise ApnsDeliveryError(
        f"APNs delivery failed: status={result.status} reason={reason}",
        status=result.status,
        reason=reason,
        invalid_token=invalid,
    )


async def send_apns_notifications(
    device_tokens: list[str],
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Send the same APNs notification to many device tokens.

    Continues after individual failures so one bad token cannot abort the batch.
    """
    successful_count = 0
    failed_count = 0
    failed_tokens: list[str] = []

    for raw_token in device_tokens or []:
        token = (raw_token or "").strip()
        if not token:
            continue
        try:
            await send_apns_notification(token, title, body, data)
            successful_count += 1
        except Exception as exc:
            failed_count += 1
            failed_tokens.append(token)
            if not is_invalid_apns_token_error(exc):
                logger.error(
                    "APNs batch send continuing after failure for token=%s: %s",
                    token,
                    exc,
                )

    return {
        "successful_count": successful_count,
        "failed_count": failed_count,
        "failed_tokens": failed_tokens,
    }
