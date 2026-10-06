"""Auto-moderation scan helper used by tests and leftover compatibility callers.

Periodic execution is owned by Celery Beat (`kampulynk.moderation.tick`).
"""

from __future__ import annotations

import logging

from apps.moderation.config import settings as auto_moderation_settings
from apps.moderation.services.auto_moderation_service import run_auto_moderation_scan

logger = logging.getLogger(__name__)


async def process_auto_moderation() -> None:
    """One scheduler tick: scan posts/comments against the configured blacklist."""
    if not auto_moderation_settings.enabled:
        return

    batch_size = auto_moderation_settings.batch_size
    logger.info("[auto-moderation] Tick started batch_size=%s", batch_size)
    try:
        await run_auto_moderation_scan(batch_size=batch_size)
    except Exception:
        logger.exception("Auto-moderation cron cycle failed")


async def cron_auto_moderation() -> None:
    """Backward-compatible alias for ``process_auto_moderation``."""
    await process_auto_moderation()
