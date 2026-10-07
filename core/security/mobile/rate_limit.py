"""Mobile-specific Redis rate limiting.

Separate from admin ``admin-signing:rate:`` / ``admin-rl:`` namespaces.
Fails closed when Redis is unavailable (security-sensitive enforcement).
"""

from __future__ import annotations

import logging
import time
from uuid import UUID, uuid4

from common.exceptions import ApiError
from core.cache.redis_client import close_redis_client, get_redis_client
from core.security.mobile.config import settings as mobile_settings
from core.security.mobile.store import GENERIC_AUTH_FAILURE

logger = logging.getLogger(__name__)

_RATE_PREFIX = "mobile-security:rate:"

RATE_LIMIT_MESSAGE = "Rate limit exceeded"


def _rate_key(*, user_id: UUID | str, device_id: str) -> str:
    return f"{_RATE_PREFIX}{user_id}:{device_id}"


async def _require_redis():
    client = await get_redis_client()
    if client is None:
        raise ApiError(GENERIC_AUTH_FAILURE)
    return client


async def consume_mobile_rate_limit(*, user_id: UUID, device_id: str) -> bool:
    """Return True when the request is allowed, False when rate-limited.

    Raises ``ApiError`` when Redis is unavailable (fail closed).
    """
    limit = max(1, int(mobile_settings.mobile_rate_limit_requests))
    window = max(1, int(mobile_settings.mobile_rate_limit_window_seconds))
    redis_key = _rate_key(user_id=user_id, device_id=device_id)
    now = time.time()
    # Unique member so concurrent requests in the same second do not collide.
    member = f"{now}:{uuid4()}"

    client = await _require_redis()
    try:
        pipe = client.pipeline()
        pipe.zremrangebyscore(redis_key, 0, now - window)
        pipe.zadd(redis_key, {member: now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window)
        results = await pipe.execute()
        count = int(results[2])
        return count <= limit
    except ApiError:
        raise
    except Exception:
        logger.warning("Failed mobile rate-limit check in Redis", exc_info=True)
        raise ApiError(GENERIC_AUTH_FAILURE)
    finally:
        await close_redis_client(client)
