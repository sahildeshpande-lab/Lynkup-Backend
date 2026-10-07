"""Android Play Integrity token verification (Google Play Integrity API)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import SecurityEventType, User, UserInstallation
from common.exceptions import ApiError
from core.security.mobile.audit import emit_mobile_security_event
from core.security.mobile.config import settings as mobile_settings
from core.security.mobile.device import MobileSecurityContext
from core.security.mobile.hmac_keys import ensure_installation_hmac_secret
from core.security.mobile.request_proof import hash_request_body
from core.security.mobile.store import GENERIC_AUTH_FAILURE, claim_integrity_request_nonce

logger = logging.getLogger(__name__)

HEADER_PLAY_INTEGRITY_TOKEN = "X-Play-Integrity-Token"

# Verdicts accepted as sufficiently intact for app recognition.
_ACCEPTABLE_APP_RECOGNITION = frozenset({"PLAY_RECOGNIZED", "UNRECOGNIZED_VERSION"})
_ACCEPTABLE_DEVICE = frozenset(
    {
        "MEETS_DEVICE_INTEGRITY",
        "MEETS_BASIC_INTEGRITY",
        "MEETS_STRONG_INTEGRITY",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_digest(value: str) -> str:
    return (value or "").strip().lower().replace(":", "").replace(" ", "")


def _load_service_account_info() -> dict[str, Any]:
    raw = (mobile_settings.play_integrity_service_account_json or "").strip()
    if not raw:
        raise ApiError(GENERIC_AUTH_FAILURE)
    if os.path.isfile(raw):
        with open(raw, encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(raw)


async def decode_integrity_token(token: str, package_name: str) -> dict[str, Any]:
    """Call Google Play Integrity decodeIntegrityToken. Fail closed on errors."""
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError as exc:
        logger.error("google-auth is required for Play Integrity verification")
        raise ApiError(GENERIC_AUTH_FAILURE) from exc

    try:
        info = _load_service_account_info()
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/playintegrity"],
        )
        session = AuthorizedSession(credentials)
        url = (
            "https://playintegrity.googleapis.com/v1/"
            f"{package_name}:decodeIntegrityToken"
        )
        response = session.post(url, json={"integrityToken": token}, timeout=15)
        if response.status_code != 200:
            logger.warning(
                "Play Integrity decode failed status=%s",
                response.status_code,
            )
            raise ApiError(GENERIC_AUTH_FAILURE)
        payload = response.json()
        token_payload = payload.get("tokenPayloadExternal") or payload
        if not isinstance(token_payload, dict):
            raise ApiError(GENERIC_AUTH_FAILURE)
        return token_payload
    except ApiError:
        raise
    except Exception:
        logger.warning("Play Integrity Google verification unavailable", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)


def _request_hash_hex(request: Request, body: bytes) -> str:
    # Bind Integrity requestHash to raw body SHA-256 (same as canonical BODY_HASH).
    return hash_request_body(body)


def validate_integrity_payload(
    payload: dict[str, Any],
    *,
    expected_package: str,
    expected_digests: list[str],
    expected_request_hash: str,
) -> str:
    """Validate package, cert digest, request hash, and integrity verdicts.

    Returns a compact integrity level string for persistence.
    """
    request_details = payload.get("requestDetails") or {}
    app_integrity = payload.get("appIntegrity") or {}
    device_integrity = payload.get("deviceIntegrity") or {}

    pkg = (app_integrity.get("packageName") or request_details.get("requestPackageName") or "").strip()
    if pkg != expected_package:
        raise ApiError(GENERIC_AUTH_FAILURE)

    certs = app_integrity.get("certificateSha256Digest") or []
    if isinstance(certs, str):
        certs = [certs]
    normalized_certs = {_normalize_digest(c) for c in certs}
    expected = {_normalize_digest(d) for d in expected_digests}
    if not expected or not normalized_certs.intersection(expected):
        raise ApiError(GENERIC_AUTH_FAILURE)

    # requestHash may be hex or base64 depending on client encoding; accept hex match.
    token_hash = (
        request_details.get("requestHash")
        or request_details.get("nonce")
        or ""
    )
    token_hash_norm = _normalize_digest(str(token_hash))
    expected_norm = _normalize_digest(expected_request_hash)
    if not token_hash_norm or token_hash_norm != expected_norm:
        # Also accept raw equality for base64url hashes clients may send.
        if str(token_hash).strip() != expected_request_hash.strip():
            raise ApiError(GENERIC_AUTH_FAILURE)

    app_recognition = (app_integrity.get("appRecognitionVerdict") or "").strip().upper()
    if app_recognition and app_recognition not in _ACCEPTABLE_APP_RECOGNITION:
        # Empty is treated as fail — require a recognized verdict when present.
        raise ApiError(GENERIC_AUTH_FAILURE)
    if not app_recognition:
        raise ApiError(GENERIC_AUTH_FAILURE)

    verdicts = device_integrity.get("deviceRecognitionVerdict") or []
    if isinstance(verdicts, str):
        verdicts = [verdicts]
    verdict_set = {str(v).strip().upper() for v in verdicts}
    if not verdict_set.intersection(_ACCEPTABLE_DEVICE):
        raise ApiError(GENERIC_AUTH_FAILURE)

    level = sorted(verdict_set.intersection(_ACCEPTABLE_DEVICE))[0]
    return f"{app_recognition}:{level}"


async def verify_android_play_integrity(
    db: AsyncSession,
    request: Request,
    user: User,
    ctx: MobileSecurityContext,
    *,
    body: bytes,
) -> None:
    """Enforce Play Integrity when ``ANDROID_INTEGRITY_ENABLED``."""
    if not mobile_settings.android_integrity_enabled:
        return

    package = (mobile_settings.play_integrity_package_name or "").strip()
    digests = mobile_settings.play_integrity_certificate_digest_list
    if not package or not digests or not (
        mobile_settings.play_integrity_service_account_json or ""
    ).strip():
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.ANDROID_INTEGRITY_FAILED,
            request=request,
            metadata={"reason": "misconfigured", "device_id": ctx.device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    token = (request.headers.get(HEADER_PLAY_INTEGRITY_TOKEN) or "").strip()
    if not token:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.ANDROID_INTEGRITY_FAILED,
            request=request,
            metadata={"reason": "missing_token", "device_id": ctx.device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    request_hash = _request_hash_hex(request, body)
    # One-time use of this request hash / token binding for the device.
    claimed = await claim_integrity_request_nonce(
        user_id=user.id,
        device_id=ctx.device_id,
        nonce=request_hash,
    )
    if not claimed:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.ANDROID_INTEGRITY_FAILED,
            request=request,
            metadata={"reason": "request_hash_reuse", "device_id": ctx.device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    try:
        payload = await decode_integrity_token(token, package)
        level = validate_integrity_payload(
            payload,
            expected_package=package,
            expected_digests=digests,
            expected_request_hash=request_hash,
        )
    except ApiError:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.ANDROID_INTEGRITY_FAILED,
            request=request,
            metadata={"reason": "verification_failed", "device_id": ctx.device_id},
        )
        raise

    installation = ctx.installation
    installation.android_package_name = package
    # Persist the matching digest from config (validated against token).
    installation.android_certificate_digest = digests[0]
    installation.android_integrity_level = level
    installation.android_last_verified_at = _utc_now()
    ensure_installation_hmac_secret(installation)
    db.add(installation)

    await emit_mobile_security_event(
        db,
        user.id,
        SecurityEventType.ANDROID_INTEGRITY_VERIFIED,
        request=request,
        metadata={
            "device_id": ctx.device_id,
            "integrity_level": level,
            "package_name": package,
        },
    )
    request.state.mobile_request_bound = True
