"""Safe mobile security event emission (never logs secrets)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import SecurityEventType
from apps.accounts.services.common_service import log_security_event

logger = logging.getLogger(__name__)

_REDACT_KEYS = frozenset(
    {
        "signature",
        "authorization",
        "access_token",
        "refresh_token",
        "firebase",
        "firebase_token",
        "id_token",
        "hmac",
        "hmac_secret",
        "mobile_hmac_secret",
        "private_key",
        "password",
        "body",
        "integrity_token",
        "play_integrity_token",
        "assertion",
        "attestation",
        "service_account",
        "credentials",
        "PLAY_INTEGRITY_SERVICE_ACCOUNT_JSON",
    }
)


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    if request.client:
        return request.client.host
    return None


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in metadata.items():
        if str(key).lower() in _REDACT_KEYS or any(
            forbidden in str(key).lower() for forbidden in ("token", "secret", "signature", "assertion")
        ):
            continue
        cleaned[key] = value
    return cleaned or None


async def emit_mobile_security_event(
    db: AsyncSession | None,
    user_id: UUID | None,
    event_type: SecurityEventType,
    *,
    request: Request | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if db is None or user_id is None:
        return
    try:
        await log_security_event(
            db,
            user_id,
            event_type,
            event_metadata=_safe_metadata(metadata),
            ip_address=_client_ip(request),
        )
    except Exception:
        logger.exception(
            "Failed to write mobile security event type=%s user_id=%s",
            event_type,
            user_id,
        )
