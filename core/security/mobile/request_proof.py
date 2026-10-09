"""Mobile request proof: canonicalization, timestamp, nonce, HMAC verification.

This is intentionally separate from Web Admin RSA-PSS signing.

Canonical form (LF newlines, no trailing newline after DEVICE_ID)::

    HTTP_METHOD
    REQUEST_PATH
    QUERY_STRING
    BODY_HASH
    TIMESTAMP
    NONCE
    DEVICE_ID

HMAC-SHA256 over UTF-8 canonical bytes; signature is standard base64.

Play Integrity ``requestHash`` (when enabled on every API) is
``SHA-256(canonical_utf8_bytes).hexdigest()`` — same canonical fields —
so empty-body GETs remain unique per timestamp/nonce.

**No global/static app-wide HMAC secret.** Keys come from per-device
``user_installations.mobile_hmac_secret`` (issued at enrollment).

When platform attestation has already bound the request
(``request.state.mobile_request_bound``), HMAC may be skipped if no key exists
yet — attestation request-hash / assertion binding is the proof. Timestamp
and nonce still apply whenever ``MOBILE_SECURITY_ENABLED``.

Raw HMAC signatures are never persisted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote, urlencode

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import SecurityEventType, User, UserInstallation
from common.exceptions import ApiError
from core.security.mobile.audit import emit_mobile_security_event
from core.security.mobile.config import settings as mobile_settings
from core.security.mobile.hmac_keys import hmac_key_from_installation
from core.security.mobile.rate_limit import RATE_LIMIT_MESSAGE, consume_mobile_rate_limit
from core.security.mobile.store import auth_failure, claim_nonce

logger = logging.getLogger(__name__)

HEADER_DEVICE_ID = "X-Device-Id"
HEADER_TIMESTAMP = "X-Timestamp"
HEADER_NONCE = "X-Nonce"
HEADER_SIGNATURE = "X-Signature"

EMPTY_BODY_SHA256 = hashlib.sha256(b"").hexdigest()


def normalize_http_method(method: str) -> str:
    return (method or "").strip().upper()


def normalize_query_string(query_string: str | None) -> str:
    raw = (query_string or "").lstrip("?")
    if not raw:
        return ""
    pairs = parse_qsl(raw, keep_blank_values=True)
    pairs.sort(key=lambda item: (item[0], item[1]))
    return urlencode(pairs, quote_via=quote, safe="")


def hash_request_body(body: bytes | None) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def build_canonical_request(
    *,
    method: str,
    path: str,
    query_string: str | None,
    body: bytes | None,
    timestamp: str | int,
    nonce: str,
    device_id: str,
) -> str:
    components = [
        normalize_http_method(method),
        path or "",
        normalize_query_string(query_string),
        hash_request_body(body),
        str(timestamp).strip(),
        (nonce or "").strip(),
        (device_id or "").strip(),
    ]
    return "\n".join(components)


def canonical_request_bytes(**kwargs) -> bytes:
    return build_canonical_request(**kwargs).encode("utf-8")


def compute_hmac_signature(*, key: bytes, message: bytes) -> str:
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_hmac_signature(*, key: bytes, message: bytes, signature_b64: str) -> bool:
    try:
        expected = compute_hmac_signature(key=key, message=message)
        return hmac.compare_digest(expected, (signature_b64 or "").strip())
    except Exception:
        return False


async def resolve_device_hmac_key(
    user: User,
    device_id: str,
    *,
    installation: UserInstallation | None = None,
) -> bytes | None:
    """Return per-device HMAC key from the installation when available."""
    _ = (user, device_id)
    return hmac_key_from_installation(installation)


def _header(request: Request, name: str) -> str:
    return (request.headers.get(name) or "").strip()


def _parse_timestamp(raw: str) -> int:
    if not raw or not raw.isdigit():
        raise auth_failure("timestamp_invalid")
    try:
        return int(raw)
    except ValueError as exc:
        raise auth_failure("timestamp_invalid") from exc


def _validate_timestamp(request_ts: int) -> None:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    tolerance = max(0, int(mobile_settings.mobile_hmac_timestamp_tolerance_seconds))
    if abs(now_ts - request_ts) > tolerance:
        raise auth_failure("timestamp_invalid")


async def verify_mobile_request_proof(
    request: Request,
    user: User,
    *,
    installation: UserInstallation | None = None,
    skip_rate_limit: bool = False,
    db: AsyncSession | None = None,
) -> None:
    """Enforce mobile timestamp + nonce + HMAC when ``MOBILE_SECURITY_ENABLED``.

    Signature matches the auth.py extension point: ``(request, user)``.
    Optional kwargs are used by the mobile orchestrator only.
    """
    if not mobile_settings.mobile_security_enabled:
        return

    device_id = _header(request, HEADER_DEVICE_ID)
    timestamp_raw = _header(request, HEADER_TIMESTAMP)
    nonce = _header(request, HEADER_NONCE)
    signature = _header(request, HEADER_SIGNATURE)

    if not device_id or not timestamp_raw or not nonce:
        if db is not None:
            await emit_mobile_security_event(
                db,
                user.id,
                SecurityEventType.MOBILE_REQUEST_PROOF_INVALID,
                request=request,
                metadata={"reason": "missing_proof_headers", "device_id": device_id or None},
            )
        raise auth_failure("missing_proof_headers")

    try:
        request_ts = _parse_timestamp(timestamp_raw)
        _validate_timestamp(request_ts)
    except ApiError:
        if db is not None:
            await emit_mobile_security_event(
                db,
                user.id,
                SecurityEventType.MOBILE_TIMESTAMP_INVALID,
                request=request,
                metadata={"reason": "timestamp_invalid", "device_id": device_id},
            )
        raise

    try:
        claimed = await claim_nonce(user_id=user.id, device_id=device_id, nonce=nonce)
    except ApiError:
        if db is not None:
            await emit_mobile_security_event(
                db,
                user.id,
                SecurityEventType.MOBILE_SECURITY_REDIS_UNAVAILABLE,
                request=request,
                metadata={"reason": "nonce_redis_unavailable", "device_id": device_id},
            )
        raise
    if not claimed:
        if db is not None:
            await emit_mobile_security_event(
                db,
                user.id,
                SecurityEventType.MOBILE_NONCE_REPLAY,
                request=request,
                metadata={"reason": "nonce_replay", "device_id": device_id},
            )
        raise auth_failure("nonce_replay")

    request_bound = bool(getattr(request.state, "mobile_request_bound", False))
    key = await resolve_device_hmac_key(user, device_id, installation=installation)

    body = await request.body()
    message = canonical_request_bytes(
        method=request.method,
        path=request.url.path,
        query_string=request.url.query,
        body=body,
        timestamp=request_ts,
        nonce=nonce,
        device_id=device_id,
    )

    if key is not None:
        if not signature or not verify_hmac_signature(
            key=key, message=message, signature_b64=signature
        ):
            if db is not None:
                await emit_mobile_security_event(
                    db,
                    user.id,
                    SecurityEventType.MOBILE_REQUEST_PROOF_INVALID,
                    request=request,
                    metadata={"reason": "invalid_hmac", "device_id": device_id},
                )
            raise auth_failure("invalid_hmac")
    elif request_bound:
        # Attestation already bound this request; HMAC optional until key issued.
        pass
    else:
        logger.warning(
            "Mobile HMAC enforcement enabled but no per-device key for user_id=%s device_id=%s",
            user.id,
            device_id,
        )
        if db is not None:
            await emit_mobile_security_event(
                db,
                user.id,
                SecurityEventType.MOBILE_REQUEST_PROOF_INVALID,
                request=request,
                metadata={"reason": "missing_hmac_key", "device_id": device_id},
            )
        raise auth_failure("missing_hmac_key")

    if not skip_rate_limit:
        try:
            allowed = await consume_mobile_rate_limit(user_id=user.id, device_id=device_id)
        except ApiError:
            if db is not None:
                await emit_mobile_security_event(
                    db,
                    user.id,
                    SecurityEventType.MOBILE_SECURITY_REDIS_UNAVAILABLE,
                    request=request,
                    metadata={"reason": "rate_limit_redis_unavailable", "device_id": device_id},
                )
            raise
        if not allowed:
            if db is not None:
                await emit_mobile_security_event(
                    db,
                    user.id,
                    SecurityEventType.MOBILE_RATE_LIMIT_EXCEEDED,
                    request=request,
                    metadata={"device_id": device_id},
                )
            raise ApiError(RATE_LIMIT_MESSAGE)

    request.state.mobile_device_id = device_id
    if db is not None:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.MOBILE_REQUEST_VERIFIED,
            request=request,
            metadata={"device_id": device_id, "request_bound": request_bound},
        )
