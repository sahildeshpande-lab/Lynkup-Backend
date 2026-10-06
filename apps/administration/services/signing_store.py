"""Redis-backed pending-key / nonce / rate-limit store for admin request signing.

Nonce and rate-limit keys are scoped by independent admin ``session_id`` so
concurrent browser sessions for the same user do not collide.

Redis is required: signed-request nonce/rate/pending operations fail closed
if Redis is unavailable.
"""

from __future__ import annotations

import logging
import time
from uuid import UUID

from common.exceptions import ApiError
from core.auth.config import settings as auth_settings
from core.cache.redis_client import close_redis_client, get_redis_client

logger = logging.getLogger(__name__)

_PENDING_PREFIX = "admin-signing:pending:"
_NONCE_PREFIX = "admin-signing:nonce:"
_RATE_PREFIX = "admin-signing:rate:"
_GENERIC_RATE_PREFIX = "admin-rl:"

GENERIC_AUTH_FAILURE = "Request authentication failed"


def _pending_key(key_id: UUID | str) -> str:
    return f"{_PENDING_PREFIX}{key_id}"


def _nonce_key(session_id: UUID | str, nonce: str) -> str:
    return f"{_NONCE_PREFIX}{session_id}:{nonce}"


def _rate_key(session_id: UUID | str) -> str:
    return f"{_RATE_PREFIX}{session_id}"


async def _require_redis():
    client = await get_redis_client()
    if client is None:
        raise ApiError(GENERIC_AUTH_FAILURE)
    return client


async def store_pending_public_key(key_id: UUID, public_key_pem: str) -> int:
    """Store a pre-login public key. Returns TTL seconds."""
    ttl = max(1, int(auth_settings.admin_signing_pending_ttl_seconds))
    redis_key = _pending_key(key_id)
    client = await _require_redis()
    try:
        await client.set(redis_key, public_key_pem, ex=ttl)
        return ttl
    except Exception:
        logger.warning("Failed to store pending admin signing key in Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)


async def pop_pending_public_key(key_id: UUID) -> str | None:
    """Atomically read+delete a pending public key registration."""
    redis_key = _pending_key(key_id)
    client = await _require_redis()
    try:
        getdel = getattr(client, "getdel", None)
        if getdel is not None:
            value = await getdel(redis_key)
        else:
            value = await client.get(redis_key)
            if value is not None:
                await client.delete(redis_key)
        if value:
            return str(value)
        return None
    except Exception:
        logger.warning("Failed to pop pending admin signing key from Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)


async def claim_nonce(*, session_id: UUID, nonce: str) -> bool:
    """Atomically claim a nonce. Returns True if newly claimed, False if replay."""
    ttl = max(1, int(auth_settings.admin_signing_nonce_ttl_seconds))
    redis_key = _nonce_key(session_id, nonce)
    client = await _require_redis()
    try:
        created = await client.set(redis_key, "1", nx=True, ex=ttl)
        return bool(created)
    except Exception:
        logger.warning("Failed to claim admin signing nonce in Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)


async def _consume_rate(redis_key: str, *, limit: int, window: int) -> bool:
    now = time.time()
    client = await _require_redis()
    try:
        pipe = client.pipeline()
        pipe.zremrangebyscore(redis_key, 0, now - window)
        pipe.zadd(redis_key, {f"{now}": now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window)
        results = await pipe.execute()
        count = int(results[2])
        return count <= limit
    except Exception:
        logger.warning("Failed admin rate-limit check in Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)


async def consume_rate_limit(session_id: UUID) -> bool:
    """Return True when the signed request is allowed, False when rate-limited."""
    limit = max(1, int(auth_settings.admin_signing_rate_limit_requests))
    window = max(1, int(auth_settings.admin_signing_rate_limit_window_seconds))
    return await _consume_rate(_rate_key(session_id), limit=limit, window=window)


async def consume_keyed_rate_limit(key: str, *, limit: int, window_seconds: int) -> bool:
    """Generic IP/email keyed rate limit for login/register/forgot-password."""
    limit = max(1, int(limit))
    window = max(1, int(window_seconds))
    return await _consume_rate(f"{_GENERIC_RATE_PREFIX}{key}", limit=limit, window=window)
