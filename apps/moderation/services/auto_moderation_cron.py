"""In-process auto-moderation cron loop (FastAPI lifespan pattern)."""

from __future__ import annotations

import asyncio
import logging

from apps.moderation.config import settings as auto_moderation_settings
from apps.moderation.services.auto_moderation_service import run_auto_moderation_scan

logger = logging.getLogger(__name__)


async def cron_auto_moderation() -> None:
    """Periodically scan posts/comments against the configured blacklist."""
    if not auto_moderation_settings.enabled:
        logger.info(
            "Auto-moderation cron not started (AUTO_MODERATION_ENABLED=false)."
        )
        return

    interval = auto_moderation_settings.cron_interval_seconds
    batch_size = auto_moderation_settings.batch_size
    logger.info(
        "Starting auto-moderation cron (interval=%ss, batch_size=%s)...",
        interval,
        batch_size,
    )
    try:
        while True:
            try:
                await run_auto_moderation_scan(batch_size=batch_size)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Auto-moderation cron cycle failed")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Auto-moderation cron task cancelled.")
        raise
