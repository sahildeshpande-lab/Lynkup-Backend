"""Android Play Integrity token verification (Google Play Integrity API)."""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import SecurityEventType, User
from common.exceptions import ApiError
from core.security.mobile.audit import emit_mobile_security_event
from core.security.mobile.config import settings as mobile_settings
from core.security.mobile.device import MobileSecurityContext
from core.security.mobile.hmac_keys import ensure_installation_hmac_secret
from core.security.mobile.request_proof import hash_request_body
from core.security.mobile.store import (
    auth_failure,
    auth_failure_reason,
    claim_integrity_request_nonce,
)

logger = logging.getLogger(__name__)

HEADER_PLAY_INTEGRITY_TOKEN = "X-Play-Integrity-Token"
_PLAY_INTEGRITY_SCOPE = "https://www.googleapis.com/auth/playintegrity"
_DECODE_TIMEOUT_SECONDS = 15

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_FIREBASE_CREDENTIALS_FILE = _REPO_ROOT / "credentials" / "firebase-adminsdk.json"

# Verdicts accepted as sufficiently intact for app recognition.
_ACCEPTABLE_APP_RECOGNITION = frozenset({"PLAY_RECOGNIZED", "UNRECOGNIZED_VERSION"})
_ACCEPTABLE_DEVICE = frozenset(
    {
        "MEETS_DEVICE_INTEGRITY",
        "MEETS_BASIC_INTEGRITY",
        "MEETS_STRONG_INTEGRITY",
    }
)

_session_lock = threading.Lock()
_authorized_session: Any | None = None
_authorized_session_fingerprint: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_digest(value: str) -> str:
    return (value or "").strip().lower().replace(":", "").replace(" ", "")


def _resolve_firebase_credentials_path() -> Path | None:
    """Resolve file-path credentials using the same conventions as Firebase init."""
    from core.auth.config import settings as auth_settings

    credential_path = auth_settings.firebase_credential_path
    if credential_path:
        cred_file = Path(credential_path)
        if not cred_file.is_absolute():
            cred_file = _REPO_ROOT / cred_file
        return cred_file
    return _DEFAULT_FIREBASE_CREDENTIALS_FILE


def firebase_play_integrity_credentials_configured() -> bool:
    """True when Firebase service-account credentials are available for Play Integrity."""
    raw = (os.getenv("FIREBASE_CREDENTIALS_JSON") or "").strip()
    if raw:
        return True
    cred_file = _resolve_firebase_credentials_path()
    return bool(cred_file and cred_file.is_file())


def _load_service_account_info() -> dict[str, Any]:
    """Load Firebase service-account JSON for Play Integrity OAuth.

    Prefers ``FIREBASE_CREDENTIALS_JSON`` (inline JSON string), then the same
    file-path fallbacks used by ``initialize_firebase_app``.
    """
    raw = (os.getenv("FIREBASE_CREDENTIALS_JSON") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("FIREBASE_CREDENTIALS_JSON is not valid JSON")
            raise auth_failure("invalid_credentials_json") from exc
        if not isinstance(data, dict):
            raise auth_failure("invalid_credentials_json")
        return data

    cred_file = _resolve_firebase_credentials_path()
    if cred_file is None or not cred_file.is_file():
        logger.warning("Firebase credentials not available for Play Integrity")
        raise auth_failure("missing_credentials")

    try:
        with open(cred_file, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load Firebase credentials file for Play Integrity")
        raise auth_failure("credentials_load_failed") from exc
    if not isinstance(data, dict):
        raise auth_failure("invalid_credentials_json")
    return data


def _credentials_fingerprint(info: dict[str, Any]) -> str:
    """Stable non-secret fingerprint so credential rotation rebuilds the session."""
    return f"{info.get('client_email', '')}|{info.get('project_id', '')}|{info.get('private_key_id', '')}"


def _get_authorized_session():
    """Return a cached AuthorizedSession; refresh credentials only when rotated."""
    global _authorized_session, _authorized_session_fingerprint

    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError as exc:
        logger.error("google-auth is required for Play Integrity verification")
        raise auth_failure("google_auth_import_missing") from exc

    info = _load_service_account_info()
    fingerprint = _credentials_fingerprint(info)

    with _session_lock:
        if (
            _authorized_session is not None
            and _authorized_session_fingerprint == fingerprint
        ):
            return _authorized_session

        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=[_PLAY_INTEGRITY_SCOPE],
        )
        _authorized_session = AuthorizedSession(credentials)
        _authorized_session_fingerprint = fingerprint
        return _authorized_session


def reset_play_integrity_session_cache() -> None:
    """Clear cached OAuth session (for tests)."""
    global _authorized_session, _authorized_session_fingerprint
    with _session_lock:
        _authorized_session = None
        _authorized_session_fingerprint = None


def play_integrity_decode_url(package_name: str) -> str:
    return (
        "https://playintegrity.googleapis.com/v1/"
        f"{package_name}:decodeIntegrityToken"
    )


def extract_integrity_token(request: Request, body: bytes) -> str:
    """Read token from ``X-Play-Integrity-Token`` or JSON body fields."""
    token = (request.headers.get(HEADER_PLAY_INTEGRITY_TOKEN) or "").strip()
    if token:
        return token
    if not body:
        return ""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    for key in (
        "integrityToken",
        "integrity_token",
        "playIntegrityToken",
        "play_integrity_token",
    ):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def decode_integrity_token(token: str, package_name: str) -> dict[str, Any]:
    """Call Google Play Integrity decodeIntegrityToken. Fail closed on errors."""
    try:
        session = _get_authorized_session()
        url = play_integrity_decode_url(package_name)
        # Google REST API uses camelCase integrityToken (not snake_case).
        response = session.post(
            url,
            json={"integrityToken": token},
            timeout=_DECODE_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.warning(
                "Play Integrity decode failed status=%s",
                response.status_code,
            )
            raise auth_failure(f"decode_http_{response.status_code}")
        payload = response.json()
        token_payload = payload.get("tokenPayloadExternal") or payload
        if not isinstance(token_payload, dict):
            raise auth_failure("invalid_decode_payload")
        return token_payload
    except ApiError:
        raise
    except Exception:
        logger.warning("Play Integrity Google verification unavailable", exc_info=True)
        raise auth_failure("google_verification_unavailable")


def _request_hash_hex(request: Request, body: bytes) -> str:
    # Bind Integrity requestHash to raw body SHA-256 (same as canonical BODY_HASH).
    return hash_request_body(body)


def validate_integrity_payload(
    payload: dict[str, Any],
    *,
    expected_package: str,
    expected_digests: list[str] | None,
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
        raise auth_failure("package_mismatch")

    certs = app_integrity.get("certificateSha256Digest") or []
    if isinstance(certs, str):
        certs = [certs]
    normalized_certs = {_normalize_digest(c) for c in certs}
    expected = {_normalize_digest(d) for d in (expected_digests or [])}
    if expected and not normalized_certs.intersection(expected):
        raise auth_failure("certificate_digest_mismatch")

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
            raise auth_failure("request_hash_mismatch")

    app_recognition = (app_integrity.get("appRecognitionVerdict") or "").strip().upper()
    if not app_recognition:
        raise auth_failure("missing_app_recognition")
    if app_recognition not in _ACCEPTABLE_APP_RECOGNITION:
        raise auth_failure(f"app_recognition_rejected:{app_recognition}")

    verdicts = device_integrity.get("deviceRecognitionVerdict") or []
    if isinstance(verdicts, str):
        verdicts = [verdicts]
    verdict_set = {str(v).strip().upper() for v in verdicts}
    if not verdict_set.intersection(_ACCEPTABLE_DEVICE):
        joined = ",".join(sorted(verdict_set)) or "none"
        raise auth_failure(f"device_integrity_rejected:{joined}")

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
    if not package or not digests or not firebase_play_integrity_credentials_configured():
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.ANDROID_INTEGRITY_FAILED,
            request=request,
            metadata={"reason": "misconfigured", "device_id": ctx.device_id},
        )
        raise auth_failure("misconfigured")

    token = extract_integrity_token(request, body)
    if not token:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.ANDROID_INTEGRITY_FAILED,
            request=request,
            metadata={"reason": "missing_token", "device_id": ctx.device_id},
        )
        raise auth_failure("missing_token")

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
        raise auth_failure("request_hash_reuse")

    try:
        payload = await decode_integrity_token(token, package)
        level = validate_integrity_payload(
            payload,
            expected_package=package,
            expected_digests=digests,
            expected_request_hash=request_hash,
        )
    except ApiError as exc:
        reason = auth_failure_reason(exc.message) or "verification_failed"
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.ANDROID_INTEGRITY_FAILED,
            request=request,
            metadata={"reason": reason, "device_id": ctx.device_id},
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
