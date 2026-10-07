"""Web Admin RSA request-signing lifecycle and verification."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import SecurityEventType, User
from apps.accounts.services.common_service import log_security_event
from apps.administration.db_models import AdminSigningKey, AdminSigningKeyStatus
from apps.administration.services.signing_canonical import canonical_request_bytes
from apps.administration.services.signing_crypto import (
    PublicKeyValidationError,
    normalize_public_key_pem,
    verify_rsa_pss_signature,
)
from apps.administration.services.signing_store import (
    claim_nonce,
    consume_rate_limit,
    pop_pending_public_key,
    store_pending_public_key,
)
from common.exceptions import ApiError
from common.schemas import ApiResponse
from core.auth.config import settings as auth_settings

logger = logging.getLogger(__name__)

GENERIC_AUTH_FAILURE = "Request authentication failed"
RATE_LIMIT_MESSAGE = "Rate limit exceeded"

HEADER_KEY_ID = "X-Key-ID"
HEADER_SESSION_ID = "X-Session-Id"
HEADER_TIMESTAMP = "X-Timestamp"
HEADER_NONCE = "X-Nonce"
HEADER_SIGNATURE = "X-Signature"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    if request.client:
        return request.client.host
    return None


async def _audit(
    db: AsyncSession,
    user_id: UUID | None,
    event_type: SecurityEventType,
    *,
    request: Request | None = None,
    metadata: dict | None = None,
) -> None:
    if user_id is None:
        return
    safe_meta = {k: v for k, v in (metadata or {}).items() if k not in {
        "signature",
        "authorization",
        "private_key",
        "refresh_token",
        "access_token",
        "password",
        "body",
    }}
    try:
        await log_security_event(
            db,
            user_id,
            event_type,
            event_metadata=safe_meta or None,
            ip_address=_client_ip(request),
        )
    except Exception:
        logger.exception("Failed to write security event type=%s user_id=%s", event_type, user_id)


async def register_pending_signing_key(public_key: str, *, request: Request | None = None) -> ApiResponse:
    """Pre-login public-key registration. Does NOT accept user_id/session_id."""
    from apps.administration.services.signing_store import consume_keyed_rate_limit

    if request is not None:
        validate_origin(request)

    ip = _client_ip(request) or "unknown"
    allowed = await consume_keyed_rate_limit(
        f"key-register:{ip}",
        limit=int(auth_settings.admin_signing_rate_limit_requests),
        window_seconds=int(auth_settings.admin_signing_rate_limit_window_seconds),
    )
    if not allowed:
        raise ApiError(RATE_LIMIT_MESSAGE)

    try:
        pem = normalize_public_key_pem(public_key)
    except PublicKeyValidationError as exc:
        return ApiResponse(status=False, message=str(exc), data=None)

    key_id = uuid4()
    ttl = await store_pending_public_key(key_id, pem)
    return ApiResponse(
        status=True,
        message="Signing key registered. Complete admin login to activate it.",
        data={
            "keyId": str(key_id),
            "expiresInSeconds": ttl,
            "algorithm": auth_settings.admin_signing_algorithm,
            "hash": auth_settings.admin_signing_hash,
            "keySize": auth_settings.admin_signing_key_size,
        },
    )


async def bind_signing_key_after_login(
    db: AsyncSession,
    user: User,
    key_id: UUID | str | None,
    *,
    session_id: UUID,
    request: Request | None = None,
) -> UUID | None:
    """Bind a pre-registered public key to authenticated (user_id, session_id).

    Pending registration has no session; the finalized row always has a real
    independent session UUID. Does not trust client-supplied user/session ids.
    """
    if key_id is None:
        return None

    try:
        key_uuid = key_id if isinstance(key_id, UUID) else UUID(str(key_id))
    except (TypeError, ValueError) as exc:
        raise ApiError("Invalid signing key registration") from exc

    public_key_pem = await pop_pending_public_key(key_uuid)
    if not public_key_pem:
        raise ApiError("Signing key registration expired or not found")

    now = utc_now()
    # One active key per session; other sessions keep their own keys.
    await db.execute(
        update(AdminSigningKey)
        .where(
            AdminSigningKey.session_id == session_id,
            AdminSigningKey.status == AdminSigningKeyStatus.ACTIVE.value,
        )
        .values(
            status=AdminSigningKeyStatus.REVOKED.value,
            revoked_at=now,
            updated_at=now,
        )
    )

    record = AdminSigningKey(
        key_id=key_uuid,
        user_id=user.id,
        session_id=session_id,
        public_key=public_key_pem,
        status=AdminSigningKeyStatus.ACTIVE.value,
        created_at=now,
        updated_at=now,
    )
    db.add(record)
    await _audit(
        db,
        user.id,
        SecurityEventType.KEY_REGISTERED,
        request=request,
        metadata={"key_id": str(key_uuid), "session_id": str(session_id)},
    )
    return key_uuid


async def revoke_signing_key(
    db: AsyncSession,
    user: User,
    key_id: UUID | str,
    *,
    request: Request | None = None,
) -> ApiResponse:
    try:
        key_uuid = key_id if isinstance(key_id, UUID) else UUID(str(key_id))
    except (TypeError, ValueError):
        return ApiResponse(status=False, message="Invalid key id", data=None)

    stmt = select(AdminSigningKey).where(
        AdminSigningKey.key_id == key_uuid,
        AdminSigningKey.user_id == user.id,
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        return ApiResponse(status=False, message="Signing key not found", data=None)

    if record.status != AdminSigningKeyStatus.REVOKED.value:
        now = utc_now()
        record.status = AdminSigningKeyStatus.REVOKED.value
        record.revoked_at = now
        record.updated_at = now
        db.add(record)
        await _audit(
            db,
            user.id,
            SecurityEventType.KEY_REVOKED,
            request=request,
            metadata={
                "key_id": str(key_uuid),
                "session_id": str(record.session_id),
            },
        )
        await db.commit()

    return ApiResponse(
        status=True,
        message="Signing key revoked",
        data={"keyId": str(key_uuid), "status": AdminSigningKeyStatus.REVOKED.value},
    )


def validate_origin(request: Request) -> None:
    allowed = auth_settings.admin_allowed_origin_list
    if not allowed:
        raise ApiError(GENERIC_AUTH_FAILURE)
    origin = (request.headers.get("origin") or "").strip()
    if not origin or origin not in allowed:
        raise ApiError(GENERIC_AUTH_FAILURE)


async def _load_active_key(
    db: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID,
    key_id: UUID,
) -> AdminSigningKey | None:
    stmt = select(AdminSigningKey).where(AdminSigningKey.key_id == key_id)
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        return None
    if record.user_id != user_id:
        return None
    if record.session_id != session_id:
        return None
    if record.status != AdminSigningKeyStatus.ACTIVE.value:
        return None
    return record


async def verify_signed_admin_request(
    request: Request,
    current_user: User,
    db: AsyncSession,
    *,
    session_id: UUID,
    skip_origin: bool = False,
) -> User:
    """Full verification chain for RSA-signed Web Admin requests.

    ``session_id`` must come from the authenticated JWT/admin_sessions row —
    never from the client body and never from user.id.
    """
    if not skip_origin:
        validate_origin(request)

    key_id_raw = (request.headers.get(HEADER_KEY_ID) or "").strip()
    session_header = (request.headers.get(HEADER_SESSION_ID) or "").strip()
    timestamp_raw = (request.headers.get(HEADER_TIMESTAMP) or "").strip()
    nonce = (request.headers.get(HEADER_NONCE) or "").strip()
    signature = (request.headers.get(HEADER_SIGNATURE) or "").strip()

    path = request.url.path
    method = request.method

    if not key_id_raw or not session_header or not timestamp_raw or not nonce or not signature:
        await _audit(
            db,
            current_user.id,
            SecurityEventType.UNAUTHORIZED_ACCESS,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "session_id": str(session_id),
                "reason": "missing_signing_headers",
            },
        )
        await db.commit()
        raise ApiError(GENERIC_AUTH_FAILURE)

    if session_header != str(session_id):
        await _audit(
            db,
            current_user.id,
            SecurityEventType.UNAUTHORIZED_ACCESS,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "session_id": str(session_id),
                "reason": "session_id_mismatch",
            },
        )
        await db.commit()
        raise ApiError(GENERIC_AUTH_FAILURE)

    try:
        key_id = UUID(key_id_raw)
    except ValueError:
        await _audit(
            db,
            current_user.id,
            SecurityEventType.UNAUTHORIZED_ACCESS,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "session_id": str(session_id),
                "reason": "invalid_key_id",
            },
        )
        await db.commit()
        raise ApiError(GENERIC_AUTH_FAILURE)

    signing_key = await _load_active_key(
        db,
        user_id=current_user.id,
        session_id=session_id,
        key_id=key_id,
    )
    if signing_key is None:
        await _audit(
            db,
            current_user.id,
            SecurityEventType.UNAUTHORIZED_ACCESS,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "key_id": str(key_id),
                "session_id": str(session_id),
                "reason": "key_not_active_for_session",
            },
        )
        await db.commit()
        raise ApiError(GENERIC_AUTH_FAILURE)

    try:
        request_ts = int(timestamp_raw)
    except ValueError:
        await _audit(
            db,
            current_user.id,
            SecurityEventType.TIMESTAMP_VALIDATION_FAILED,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "key_id": str(key_id),
                "session_id": str(session_id),
                "reason": "invalid_timestamp",
            },
        )
        await db.commit()
        raise ApiError(GENERIC_AUTH_FAILURE)

    now_ts = int(utc_now().timestamp())
    tolerance = int(auth_settings.admin_signing_timestamp_tolerance_seconds)
    if abs(now_ts - request_ts) > tolerance:
        await _audit(
            db,
            current_user.id,
            SecurityEventType.TIMESTAMP_VALIDATION_FAILED,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "key_id": str(key_id),
                "session_id": str(session_id),
                "reason": "timestamp_out_of_window",
            },
        )
        await db.commit()
        raise ApiError(GENERIC_AUTH_FAILURE)

    # Nonce before RSA (architecture order + cheaper replay reject).
    claimed = await claim_nonce(session_id=session_id, nonce=nonce)
    if not claimed:
        await _audit(
            db,
            current_user.id,
            SecurityEventType.NONCE_REPLAY_DETECTED,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "key_id": str(key_id),
                "session_id": str(session_id),
                "reason": "nonce_replay",
            },
        )
        await db.commit()
        raise ApiError(GENERIC_AUTH_FAILURE)

    body = await request.body()
    message = canonical_request_bytes(
        method=method,
        path=path,
        query_string=request.url.query,
        body=body,
        timestamp=request_ts,
        nonce=nonce,
        session_id=str(session_id),
        key_id=str(key_id),
    )

    if not verify_rsa_pss_signature(
        public_key_pem=signing_key.public_key,
        message=message,
        signature_b64=signature,
    ):
        await _audit(
            db,
            current_user.id,
            SecurityEventType.SIGNATURE_VERIFICATION_FAILED,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "key_id": str(key_id),
                "session_id": str(session_id),
                "reason": "invalid_signature",
            },
        )
        await db.commit()
        raise ApiError(GENERIC_AUTH_FAILURE)

    allowed = await consume_rate_limit(session_id)
    if not allowed:
        await _audit(
            db,
            current_user.id,
            SecurityEventType.RATE_LIMIT_EXCEEDED,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "key_id": str(key_id),
                "session_id": str(session_id),
            },
        )
        await db.commit()
        raise ApiError(RATE_LIMIT_MESSAGE)

    now = utc_now()
    await db.execute(
        update(AdminSigningKey)
        .where(AdminSigningKey.id == signing_key.id)
        .values(last_used_at=now)
    )

    # Audit mutating signed requests (avoid noise on GETs).
    if (request.method or "").upper() not in {"GET", "HEAD", "OPTIONS"}:
        await _audit(
            db,
            current_user.id,
            SecurityEventType.SIGNED_REQUEST_ACCEPTED,
            request=request,
            metadata={
                "path": path,
                "method": method,
                "key_id": str(key_id),
                "session_id": str(session_id),
            },
        )

    await db.commit()
    return current_user
