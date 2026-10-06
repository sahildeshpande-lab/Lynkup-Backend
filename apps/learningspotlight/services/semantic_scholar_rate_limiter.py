"""Distributed Semantic Scholar rate limiter (shared API key).

Uses Redis when available so multiple workers share one global request budget.
Falls back to a process-local spacing lock when Redis is unavailable (tests / local).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from apps.recommendations.config import settings as rec_settings
from core.cache.redis_client import close_redis_client, get_redis_client

logger = logging.getLogger(__name__)

_REDIS_KEY = "semantic_scholar:rate_limit:next_allowed"
_LOCAL_LOCK = asyncio.Lock()
_LOCAL_NEXT_ALLOWED = 0.0
_REDIS_CLIENT: Any | None = None
_REDIS_LOCK = asyncio.Lock()

# Lua: schedule the next allowed request time (global spacing).
# KEYS[1] = next-allowed timestamp (ms)
# ARGV[1] = now_ms, ARGV[2] = min_interval_ms
# Returns wait_ms (>= 0).
_ACQUIRE_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local next_allowed = tonumber(redis.call('GET', key) or '0')
local wait = 0
if now < next_allowed then
  wait = next_allowed - now
  next_allowed = next_allowed + interval
else
  next_allowed = now + interval
end
redis.call('SET', key, tostring(next_allowed), 'PX', math.max(interval * 4, 1000))
return wait
"""


def _min_interval_seconds(rps: float | None = None) -> float:
    rate = float(rps if rps is not None else rec_settings.semantic_scholar_rps)
    if rate <= 0:
        rate = 1.0
    return 1.0 / rate


async def _get_cached_redis() -> Any | None:
    global _REDIS_CLIENT
    async with _REDIS_LOCK:
        if _REDIS_CLIENT is not None:
            return _REDIS_CLIENT
        client = await get_redis_client()
        _REDIS_CLIENT = client
        return client


async def acquire_semantic_scholar_permit(
    *,
    rps: float | None = None,
) -> float:
    """Block until a Semantic Scholar request slot is available.

    Returns the seconds waited (0 when the slot was immediately available).
    """
    interval = _min_interval_seconds(rps)
    waited = await _acquire_redis(interval)
    if waited is not None:
        return waited
    return await _acquire_local(interval)


async def _acquire_redis(interval: float) -> float | None:
    global _REDIS_CLIENT
    client = await _get_cached_redis()
    if client is None:
        return None
    try:
        now_ms = int(time.time() * 1000)
        interval_ms = max(1, int(interval * 1000))
        wait_ms = await client.eval(
            _ACQUIRE_LUA,
            1,
            _REDIS_KEY,
            str(now_ms),
            str(interval_ms),
        )
        wait_seconds = max(0.0, float(wait_ms) / 1000.0)
        if wait_seconds > 0:
            logger.info(
                "[semantic-scholar-rate-limiter] wait=%.3fs rps=%.3f backend=redis",
                wait_seconds,
                1.0 / interval,
            )
            await asyncio.sleep(wait_seconds)
        return wait_seconds
    except Exception:
        logger.warning(
            "[semantic-scholar-rate-limiter] redis acquire failed; falling back to local",
            exc_info=True,
        )
        async with _REDIS_LOCK:
            stale = _REDIS_CLIENT
            _REDIS_CLIENT = None
        await close_redis_client(stale)
        return None


async def _acquire_local(interval: float) -> float:
    global _LOCAL_NEXT_ALLOWED
    async with _LOCAL_LOCK:
        now = time.monotonic()
        wait = max(0.0, _LOCAL_NEXT_ALLOWED - now)
        if wait > 0:
            logger.info(
                "[semantic-scholar-rate-limiter] wait=%.3fs rps=%.3f backend=local",
                wait,
                1.0 / interval,
            )
            await asyncio.sleep(wait)
            now = time.monotonic()
        _LOCAL_NEXT_ALLOWED = now + interval
        return wait


def reset_local_rate_limiter_state() -> None:
    """Test helper: clear the process-local fallback clock and cached Redis client."""
    global _LOCAL_NEXT_ALLOWED, _REDIS_CLIENT
    _LOCAL_NEXT_ALLOWED = 0.0
    _REDIS_CLIENT = None
