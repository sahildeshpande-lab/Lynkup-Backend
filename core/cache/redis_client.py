from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)


def _redis_url() -> str | None:
    url = (os.getenv("REDIS_URL") or "").strip()
    if url:
        return url
    host = (os.getenv("REDIS_HOST") or "").strip()
    if not host:
        return None
    port = (os.getenv("REDIS_PORT") or "6379").strip() or "6379"
    password = (os.getenv("REDIS_PASSWORD") or "").strip()
    if password:
        return f"redis://:{password}@{host}:{port}/0"
    return f"redis://{host}:{port}/0"


async def get_redis_client() -> Any | None:
    """Return an async Redis client, or ``None`` when Redis is unavailable."""
    url = _redis_url()
    if not url:
        return None
    try:
        import redis.asyncio as redis
    except ImportError:
        logger.debug("Redis client is not installed")
        return None
    client = None
    try:
        client = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        await client.ping()
        return client
    except Exception:
        logger.debug("Redis unavailable; continuing without it", exc_info=True)
        if client is not None:
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    try:
                        await result
                    except Exception:
                        pass
        return None


async def close_redis_client(client: Any | None) -> None:
    if client is None:
        return
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result


async def delete_keys(keys: Sequence[str]) -> None:
    """Best-effort Redis key deletion. No-ops when Redis is unavailable."""
    if not keys:
        return
    client = await get_redis_client()
    if client is None:
        return
    try:
        await client.delete(*keys)
    except Exception:
        logger.warning("Failed to invalidate Redis cache keys=%s", list(keys), exc_info=True)
    finally:
        await close_redis_client(client)
