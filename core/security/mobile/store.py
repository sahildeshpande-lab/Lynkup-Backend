"""Redis-backed nonce / challenge store for mobile request security.

Uses mobile-specific key prefixes — never ``admin-signing:*``.

When mobile security enforcement is enabled, Redis is required: operations
fail closed if Redis is unavailable (no local fallback that would allow
cross-worker nonce replay).
"""

from __future__ import annotations

import logging
import secrets
from uuid import UUID

from common.exceptions import ApiError
from core.cache.redis_client import close_redis_client, get_redis_client
from core.security.mobile.config import settings as mobile_settings

logger = logging.getLogger(__name__)

_NONCE_PREFIX = "mobile-security:nonce:"
_ATTEST_CHALLENGE_PREFIX = "mobile-security:attest-challenge:"
_INTEGRITY_NONCE_PREFIX = "mobile-security:integrity-nonce:"

GENERIC_AUTH_FAILURE = "Request authentication failed"


def _nonce_key(*, user_id: UUID | str, device_id: str, nonce: str) -> str:
    return f"{_NONCE_PREFIX}{user_id}:{device_id}:{nonce}"


def _attest_challenge_key(*, user_id: UUID | str, device_id: str) -> str:
    return f"{_ATTEST_CHALLENGE_PREFIX}{user_id}:{device_id}"


def _integrity_nonce_key(*, user_id: UUID | str, device_id: str, nonce: str) -> str:
    return f"{_INTEGRITY_NONCE_PREFIX}{user_id}:{device_id}:{nonce}"


async def require_redis():
    client = await get_redis_client()
    if client is None:
        raise ApiError(GENERIC_AUTH_FAILURE)
    return client


async def claim_nonce(
    *,
    user_id: UUID,
    device_id: str,
    nonce: str,
) -> bool:
    """Atomically claim a nonce. Returns True if newly claimed, False if replay.

    Raises ``ApiError`` when Redis is unavailable (fail closed).
    """
    ttl = max(1, int(mobile_settings.mobile_hmac_nonce_ttl_seconds))
    redis_key = _nonce_key(user_id=user_id, device_id=device_id, nonce=nonce)
    client = await require_redis()
    try:
        created = await client.set(redis_key, "1", nx=True, ex=ttl)
        return bool(created)
    except ApiError:
        raise
    except Exception:
        logger.warning("Failed to claim mobile security nonce in Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)


async def store_attest_challenge(*, user_id: UUID, device_id: str, challenge: str) -> int:
    ttl = max(1, int(mobile_settings.ios_attest_challenge_ttl_seconds))
    redis_key = _attest_challenge_key(user_id=user_id, device_id=device_id)
    client = await require_redis()
    try:
        await client.set(redis_key, challenge, ex=ttl)
        return ttl
    except ApiError:
        raise
    except Exception:
        logger.warning("Failed to store App Attest challenge in Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)


async def pop_attest_challenge(*, user_id: UUID, device_id: str) -> str | None:
    redis_key = _attest_challenge_key(user_id=user_id, device_id=device_id)
    client = await require_redis()
    try:
        getdel = getattr(client, "getdel", None)
        if getdel is not None:
            value = await getdel(redis_key)
        else:
            value = await client.get(redis_key)
            if value is not None:
                await client.delete(redis_key)
        return str(value) if value else None
    except ApiError:
        raise
    except Exception:
        logger.warning("Failed to pop App Attest challenge from Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)


async def peek_attest_challenge(*, user_id: UUID, device_id: str) -> str | None:
    redis_key = _attest_challenge_key(user_id=user_id, device_id=device_id)
    client = await require_redis()
    try:
        value = await client.get(redis_key)
        return str(value) if value else None
    except ApiError:
        raise
    except Exception:
        logger.warning("Failed to read App Attest challenge from Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)


async def claim_integrity_request_nonce(
    *,
    user_id: UUID,
    device_id: str,
    nonce: str,
) -> bool:
    """Prevent Play Integrity token / requestHash reuse across requests."""
    ttl = max(1, int(mobile_settings.mobile_hmac_nonce_ttl_seconds))
    redis_key = _integrity_nonce_key(user_id=user_id, device_id=device_id, nonce=nonce)
    client = await require_redis()
    try:
        created = await client.set(redis_key, "1", nx=True, ex=ttl)
        return bool(created)
    except ApiError:
        raise
    except Exception:
        logger.warning("Failed to claim Play Integrity nonce in Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)


def generate_challenge_bytes(nbytes: int = 32) -> bytes:
    return secrets.token_bytes(nbytes)
