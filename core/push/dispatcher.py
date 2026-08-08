from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

from core.apns import send_apns_notification, send_apns_notifications
from core.auth.services import send_push_notification, send_push_notifications

logger = logging.getLogger(__name__)

PLATFORM_ANDROID = "android"
PLATFORM_IOS = "ios"


@dataclass(frozen=True, slots=True)
class PushTarget:
    token: str
    platform: str = PLATFORM_ANDROID


def normalize_platform(platform: str | None) -> str:
    """
    Normalize installation platform for push routing.

    Unknown / missing platforms default to Android so legacy FCM tokens keep working.
    """
    value = (platform or "").strip().lower()
    if value in {"ios", "iphone", "ipad"}:
        return PLATFORM_IOS
    return PLATFORM_ANDROID


async def send_push_to_device(
    *,
    token: str,
    platform: str | None,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> str:
    """
    Send one push to a single device, routing by platform.

    Android uses the existing Firebase FCM sender unchanged.
    iOS uses direct APNs.
    """
    normalized = normalize_platform(platform)
    device_token = (token or "").strip()
    if not device_token:
        raise ValueError("token cannot be blank")

    if normalized == PLATFORM_IOS:
        return await send_apns_notification(
            device_token,
            title,
            body,
            data,
        )

    # Keep Android on the existing synchronous FCM path.
    return send_push_notification(device_token, title, body, data)


async def send_push_to_devices(
    targets: Sequence[PushTarget] | Sequence[tuple[str, str | None]],
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Send the same notification to many devices, branching by platform.

    Continues after individual failures. Return shape matches the existing FCM
    batch helper so callers stay unchanged: successful_count, failed_count,
    failed_tokens.
    """
    android_tokens: list[str] = []
    ios_tokens: list[str] = []
    seen: set[tuple[str, str]] = set()

    for item in targets or []:
        if isinstance(item, PushTarget):
            token = (item.token or "").strip()
            platform = normalize_platform(item.platform)
        else:
            raw_token, raw_platform = item
            token = (raw_token or "").strip()
            platform = normalize_platform(raw_platform)

        if not token:
            continue
        key = (platform, token)
        if key in seen:
            continue
        seen.add(key)

        if platform == PLATFORM_IOS:
            ios_tokens.append(token)
        else:
            android_tokens.append(token)

    successful_count = 0
    failed_count = 0
    failed_tokens: list[str] = []

    if android_tokens:
        # Existing Android FCM batch path — behavior unchanged.
        android_result = send_push_notifications(
            android_tokens,
            title,
            body,
            data,
        )
        successful_count += int(android_result.get("successful_count", 0))
        failed_count += int(android_result.get("failed_count", 0))
        failed_tokens.extend(android_result.get("failed_tokens") or [])
        logger.info(
            "Push dispatch android complete successful=%s failed=%s",
            android_result.get("successful_count", 0),
            android_result.get("failed_count", 0),
        )

    if ios_tokens:
        ios_result = await send_apns_notifications(
            ios_tokens,
            title,
            body,
            data,
        )
        successful_count += int(ios_result.get("successful_count", 0))
        failed_count += int(ios_result.get("failed_count", 0))
        failed_tokens.extend(ios_result.get("failed_tokens") or [])
        logger.info(
            "Push dispatch ios complete successful=%s failed=%s",
            ios_result.get("successful_count", 0),
            ios_result.get("failed_count", 0),
        )

    if not android_tokens and not ios_tokens:
        logger.info("Push dispatch skipped reason=no_targets")

    return {
        "successful_count": successful_count,
        "failed_count": failed_count,
        "failed_tokens": failed_tokens,
    }
