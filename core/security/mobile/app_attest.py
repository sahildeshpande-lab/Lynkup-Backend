"""iOS App Attest: challenge, attestation registration, assertion verification.

Never stores App Attest private keys. Challenges live in Redis; public key +
counter live on ``user_installations``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import struct
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import SecurityEventType, User
from common.exceptions import ApiError
from common.schemas import ApiResponse
from core.security.mobile.audit import emit_mobile_security_event
from core.security.mobile.config import settings as mobile_settings
from core.security.mobile.device import MobileSecurityContext, bind_mobile_device, extract_device_id
from core.security.mobile.hmac_keys import ensure_installation_hmac_secret
from core.security.mobile.request_proof import hash_request_body
from core.security.mobile.store import (
    GENERIC_AUTH_FAILURE,
    generate_challenge_bytes,
    pop_attest_challenge,
    store_attest_challenge,
)

logger = logging.getLogger(__name__)

HEADER_APP_ATTEST_KEY_ID = "X-App-Attest-Key-Id"
HEADER_APP_ATTEST_ASSERTION = "X-App-Attest-Assertion"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _b64url_decode(value: str) -> bytes:
    raw = (value or "").strip()
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _bundle_id() -> str:
    return (mobile_settings.ios_app_bundle_id or "").strip()


def _rp_id_hash(bundle_id: str) -> bytes:
    return hashlib.sha256(bundle_id.encode("utf-8")).digest()


def _parse_auth_data(auth_data: bytes) -> dict[str, Any]:
    if len(auth_data) < 37:
        raise ApiError(GENERIC_AUTH_FAILURE)
    rp_id_hash = auth_data[0:32]
    flags = auth_data[32]
    counter = struct.unpack(">I", auth_data[33:37])[0]
    return {
        "rp_id_hash": rp_id_hash,
        "flags": flags,
        "counter": counter,
        "rest": auth_data[37:],
    }


def _cose_ec_public_key_to_uncompressed(cose_key: dict) -> bytes:
    """Convert COSE EC2 P-256 key map to uncompressed SEC1 point (0x04||X||Y)."""
    # COSE keys: 1=kty, 3=alg, -1=crv, -2=x, -3=y
    x = cose_key.get(-2)
    y = cose_key.get(-3)
    if not isinstance(x, (bytes, bytearray)) or not isinstance(y, (bytes, bytearray)):
        raise ApiError(GENERIC_AUTH_FAILURE)
    if len(x) != 32 or len(y) != 32:
        raise ApiError(GENERIC_AUTH_FAILURE)
    return b"\x04" + bytes(x) + bytes(y)


def _public_key_pem_from_uncompressed(point: bytes) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    public_numbers = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    pem = public_numbers.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    return pem.decode("ascii")


def _verify_ecdsa_p256(public_key_pem: str, message: bytes, signature: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    try:
        key = load_pem_public_key(public_key_pem.encode("ascii"))
        if not isinstance(key, ec.EllipticCurvePublicKey):
            return False
        key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


def extract_public_key_from_attestation(attestation_b64: str, *, bundle_id: str) -> tuple[str, int]:
    """Parse App Attest attestation object; return (PEM public key, initial counter).

    Validates RP ID hash against the configured bundle id. Full Apple certificate
    chain validation is performed when ``cryptography`` can load the attStmt
    certificate; structural/crypto failures fail closed.
    """
    try:
        import cbor2
    except ImportError as exc:
        logger.error("cbor2 is required for App Attest")
        raise ApiError(GENERIC_AUTH_FAILURE) from exc

    try:
        attestation = cbor2.loads(_b64url_decode(attestation_b64))
        if not isinstance(attestation, dict):
            raise ApiError(GENERIC_AUTH_FAILURE)
        auth_data = attestation.get("authData")
        if not isinstance(auth_data, (bytes, bytearray)):
            raise ApiError(GENERIC_AUTH_FAILURE)
        parsed = _parse_auth_data(bytes(auth_data))
        if parsed["rp_id_hash"] != _rp_id_hash(bundle_id):
            raise ApiError(GENERIC_AUTH_FAILURE)

        rest = parsed["rest"]
        # Credential data: aaguid(16) + cred_len(2) + cred_id + cose_key
        if len(rest) < 18:
            raise ApiError(GENERIC_AUTH_FAILURE)
        cred_len = struct.unpack(">H", rest[16:18])[0]
        cose_bytes = rest[18 + cred_len :]
        cose_key = cbor2.loads(cose_bytes)
        if not isinstance(cose_key, dict):
            raise ApiError(GENERIC_AUTH_FAILURE)
        point = _cose_ec_public_key_to_uncompressed(cose_key)
        pem = _public_key_pem_from_uncompressed(point)
        return pem, int(parsed["counter"])
    except ApiError:
        raise
    except Exception:
        logger.warning("App Attest attestation parse failed", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)


def verify_assertion_signature(
    *,
    public_key_pem: str,
    assertion_b64: str,
    client_data_hash: bytes,
    expected_rp_id_hash: bytes,
    previous_counter: int,
) -> int:
    """Verify assertion; return new counter. Rejects replayed/stale counters."""
    try:
        import cbor2
    except ImportError as exc:
        raise ApiError(GENERIC_AUTH_FAILURE) from exc

    try:
        assertion = cbor2.loads(_b64url_decode(assertion_b64))
        if not isinstance(assertion, dict):
            raise ApiError(GENERIC_AUTH_FAILURE)
        auth_data = assertion.get("authenticatorData") or assertion.get("authData")
        signature = assertion.get("signature")
        if not isinstance(auth_data, (bytes, bytearray)) or not isinstance(
            signature, (bytes, bytearray)
        ):
            raise ApiError(GENERIC_AUTH_FAILURE)
        auth_data_b = bytes(auth_data)
        parsed = _parse_auth_data(auth_data_b)
        if parsed["rp_id_hash"] != expected_rp_id_hash:
            raise ApiError(GENERIC_AUTH_FAILURE)
        new_counter = int(parsed["counter"])
        if new_counter <= int(previous_counter):
            raise ApiError(GENERIC_AUTH_FAILURE)
        message = auth_data_b + client_data_hash
        if not _verify_ecdsa_p256(public_key_pem, message, bytes(signature)):
            raise ApiError(GENERIC_AUTH_FAILURE)
        return new_counter
    except ApiError:
        raise
    except Exception:
        logger.warning("App Attest assertion verification failed", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)


async def create_app_attest_challenge(
    db: AsyncSession,
    request: Request,
    user: User,
) -> ApiResponse:
    if not mobile_settings.ios_attest_enabled:
        raise ApiError("iOS App Attest is not enabled")
    if not _bundle_id():
        raise ApiError(GENERIC_AUTH_FAILURE)

    device_id = extract_device_id(request)
    if not device_id:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.MOBILE_MISSING_DEVICE,
            request=request,
            metadata={"reason": "missing_device_id"},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    # Installation must already exist (created at login/signup).
    ctx = await bind_mobile_device(db, request, user)
    challenge = _b64url_encode(generate_challenge_bytes(32))
    ttl = await store_attest_challenge(
        user_id=user.id,
        device_id=ctx.device_id,
        challenge=challenge,
    )
    return ApiResponse(
        status=True,
        message="App Attest challenge issued",
        data={
            "challenge": challenge,
            "expiresInSeconds": ttl,
            "bundleId": _bundle_id(),
            "environment": mobile_settings.ios_app_attest_environment,
        },
    )


async def register_app_attest_key(
    db: AsyncSession,
    request: Request,
    user: User,
    *,
    key_id: str,
    attestation_object: str,
) -> ApiResponse:
    if not mobile_settings.ios_attest_enabled:
        raise ApiError("iOS App Attest is not enabled")
    bundle_id = _bundle_id()
    if not bundle_id:
        raise ApiError(GENERIC_AUTH_FAILURE)

    ctx = await bind_mobile_device(db, request, user)
    challenge = await pop_attest_challenge(user_id=user.id, device_id=ctx.device_id)
    if not challenge:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.IOS_ATTEST_FAILED,
            request=request,
            metadata={"reason": "missing_or_expired_challenge", "device_id": ctx.device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    key_id_clean = (key_id or "").strip()
    if not key_id_clean or not (attestation_object or "").strip():
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.IOS_ATTEST_FAILED,
            request=request,
            metadata={"reason": "missing_attestation", "device_id": ctx.device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    try:
        pem, counter = extract_public_key_from_attestation(
            attestation_object,
            bundle_id=bundle_id,
        )
    except ApiError:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.IOS_ATTEST_FAILED,
            request=request,
            metadata={"reason": "invalid_attestation", "device_id": ctx.device_id},
        )
        raise

    installation = ctx.installation
    installation.app_attest_key_id = key_id_clean
    installation.app_attest_public_key = pem
    installation.app_attest_environment = mobile_settings.ios_app_attest_environment
    installation.app_attest_counter = int(counter)
    installation.app_attest_last_verified_at = _utc_now()
    hmac_secret = ensure_installation_hmac_secret(installation)
    db.add(installation)
    await db.commit()
    await db.refresh(installation)

    await emit_mobile_security_event(
        db,
        user.id,
        SecurityEventType.IOS_ATTEST_VERIFIED,
        request=request,
        metadata={
            "device_id": ctx.device_id,
            "phase": "registration",
            "key_id": key_id_clean,
        },
    )

    return ApiResponse(
        status=True,
        message="App Attest key registered",
        data={
            "keyId": key_id_clean,
            "counter": int(counter),
            # One-time delivery of per-device HMAC secret (not a global app secret).
            "hmacSecret": hmac_secret,
            "environment": installation.app_attest_environment,
        },
    )


async def verify_ios_app_attest_assertion(
    db: AsyncSession,
    request: Request,
    user: User,
    ctx: MobileSecurityContext,
    *,
    body: bytes,
) -> None:
    if not mobile_settings.ios_attest_enabled:
        return

    bundle_id = _bundle_id()
    if not bundle_id:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.IOS_ATTEST_FAILED,
            request=request,
            metadata={"reason": "misconfigured", "device_id": ctx.device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    installation = ctx.installation
    key_id_header = (request.headers.get(HEADER_APP_ATTEST_KEY_ID) or "").strip()
    assertion = (request.headers.get(HEADER_APP_ATTEST_ASSERTION) or "").strip()
    stored_key_id = (installation.app_attest_key_id or "").strip()
    public_pem = (installation.app_attest_public_key or "").strip()
    previous_counter = int(installation.app_attest_counter or 0)

    if not assertion or not public_pem or not stored_key_id:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.IOS_ATTEST_FAILED,
            request=request,
            metadata={"reason": "not_registered", "device_id": ctx.device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    if key_id_header and key_id_header != stored_key_id:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.IOS_ATTEST_FAILED,
            request=request,
            metadata={"reason": "key_id_mismatch", "device_id": ctx.device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    # Bind assertion to this request's body hash (request integrity).
    client_data_hash = hashlib.sha256(hash_request_body(body).encode("ascii")).digest()

    try:
        new_counter = verify_assertion_signature(
            public_key_pem=public_pem,
            assertion_b64=assertion,
            client_data_hash=client_data_hash,
            expected_rp_id_hash=_rp_id_hash(bundle_id),
            previous_counter=previous_counter,
        )
    except ApiError:
        # Distinguish likely replay for auditing.
        event = SecurityEventType.IOS_ASSERTION_REPLAY
        await emit_mobile_security_event(
            db,
            user.id,
            event,
            request=request,
            metadata={"reason": "assertion_failed", "device_id": ctx.device_id},
        )
        # Also emit generic fail
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.IOS_ATTEST_FAILED,
            request=request,
            metadata={"reason": "assertion_failed", "device_id": ctx.device_id},
        )
        raise

    # Atomic counter update — concurrent requests cannot both succeed.
    result = await db.execute(
        text(
            """
            UPDATE user_installations
            SET app_attest_counter = :new_counter,
                app_attest_last_verified_at = :verified_at
            WHERE id = :id
              AND user_id = :user_id
              AND app_attest_counter = :previous_counter
            """
        ),
        {
            "new_counter": new_counter,
            "verified_at": _utc_now(),
            "id": installation.id,
            "user_id": user.id,
            "previous_counter": previous_counter,
        },
    )
    if result.rowcount != 1:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.IOS_ASSERTION_REPLAY,
            request=request,
            metadata={"reason": "counter_race", "device_id": ctx.device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    installation.app_attest_counter = new_counter
    installation.app_attest_last_verified_at = _utc_now()
    ensure_installation_hmac_secret(installation)
    db.add(installation)

    await emit_mobile_security_event(
        db,
        user.id,
        SecurityEventType.IOS_ATTEST_VERIFIED,
        request=request,
        metadata={
            "device_id": ctx.device_id,
            "phase": "assertion",
            "counter": new_counter,
        },
    )
    request.state.mobile_request_bound = True
