"""Redis-backed pending-key / nonce / rate-limit store for admin request signing.

Falls back to process-local memory when Redis is unavailable (tests / local).
Production deployments should configure REDIS_URL so nonce replay protection is
shared across workers.

Nonce and rate-limit keys are scoped by independent admin ``session_id`` so
concurrent browser sessions for the same user do not collide.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from uuid import UUID

from core.auth.config import settings as auth_settings
from core.cache.redis_client import close_redis_client, get_redis_client

logger = logging.getLogger(__name__)

_PENDING_PREFIX = "admin-signing:pending:"
_NONCE_PREFIX = "admin-signing:nonce:"
_RATE_PREFIX = "admin-signing:rate:"

_LOCAL_LOCK = asyncio.Lock()
_LOCAL_PENDING: dict[str, tuple[str, float]] = {}
_LOCAL_NONCES: dict[str, float] = {}
_LOCAL_RATES: dict[str, list[float]] = {}


def _pending_key(key_id: UUID | str) -> str:
    return f"{_PENDING_PREFIX}{key_id}"


def _nonce_key(session_id: UUID | str, nonce: str) -> str:
    return f"{_NONCE_PREFIX}{session_id}:{nonce}"


def _rate_key(session_id: UUID | str) -> str:
    return f"{_RATE_PREFIX}{session_id}"


def _purge_local_expired(now: float) -> None:
    expired_pending = [k for k, (_, exp) in _LOCAL_PENDING.items() if exp <= now]
    for key in expired_pending:
        _LOCAL_PENDING.pop(key, None)
    expired_nonces = [k for k, exp in _LOCAL_NONCES.items() if exp <= now]
    for key in expired_nonces:
        _LOCAL_NONCES.pop(key, None)


async def store_pending_public_key(key_id: UUID, public_key_pem: str) -> int:
    """Store a pre-login public key. Returns TTL seconds."""
    ttl = max(1, int(auth_settings.admin_signing_pending_ttl_seconds))
    redis_key = _pending_key(key_id)
    client = await get_redis_client()
    if client is not None:
        try:
            await client.set(redis_key, public_key_pem, ex=ttl)
            return ttl
        except Exception:
            logger.warning("Failed to store pending admin signing key in Redis", exc_info=True)
        finally:
            await close_redis_client(client)

    async with _LOCAL_LOCK:
        _purge_local_expired(time.time())
        _LOCAL_PENDING[redis_key] = (public_key_pem, time.time() + ttl)
    return ttl


async def pop_pending_public_key(key_id: UUID) -> str | None:
    """Atomically read+delete a pending public key registration."""
    redis_key = _pending_key(key_id)
    client = await get_redis_client()
    if client is not None:
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
        except Exception:
            logger.warning("Failed to pop pending admin signing key from Redis", exc_info=True)
        finally:
            await close_redis_client(client)

    async with _LOCAL_LOCK:
        _purge_local_expired(time.time())
        item = _LOCAL_PENDING.pop(redis_key, None)
        if item is None:
            return None
        value, expires_at = item
        if expires_at <= time.time():
            return None
        return value


async def claim_nonce(*, session_id: UUID, nonce: str) -> bool:
    """Atomically claim a nonce. Returns True if newly claimed, False if replay."""
    ttl = max(1, int(auth_settings.admin_signing_nonce_ttl_seconds))
    redis_key = _nonce_key(session_id, nonce)
    client = await get_redis_client()
    if client is not None:
        try:
            created = await client.set(redis_key, "1", nx=True, ex=ttl)
            return bool(created)
        except Exception:
            logger.warning("Failed to claim admin signing nonce in Redis", exc_info=True)
        finally:
            await close_redis_client(client)

    now = time.time()
    async with _LOCAL_LOCK:
        _purge_local_expired(now)
        if redis_key in _LOCAL_NONCES and _LOCAL_NONCES[redis_key] > now:
            return False
        _LOCAL_NONCES[redis_key] = now + ttl
        return True


async def consume_rate_limit(session_id: UUID) -> bool:
    """Return True when the request is allowed, False when rate-limited."""
    limit = max(1, int(auth_settings.admin_signing_rate_limit_requests))
    window = max(1, int(auth_settings.admin_signing_rate_limit_window_seconds))
    redis_key = _rate_key(session_id)
    now = time.time()

    client = await get_redis_client()
    if client is not None:
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
            logger.warning("Failed admin signing rate-limit check in Redis", exc_info=True)
        finally:
            await close_redis_client(client)

    async with _LOCAL_LOCK:
        stamps = _LOCAL_RATES.setdefault(str(session_id), [])
        cutoff = now - window
        stamps[:] = [ts for ts in stamps if ts >= cutoff]
        stamps.append(now)
        return len(stamps) <= limit


def clear_local_signing_store() -> None:
    """Test helper to reset process-local fallback state."""
    _LOCAL_PENDING.clear()
    _LOCAL_NONCES.clear()
    _LOCAL_RATES.clear()
