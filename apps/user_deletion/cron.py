from __future__ import annotations

import asyncio
import logging

from apps.user_deletion.config import settings as deletion_settings
from apps.user_deletion.services.account_deletion_service import AccountDeletionService

logger = logging.getLogger(__name__)


async def process_expired_account_deletions() -> None:
    """One cron tick: purge users whose grace period has ended."""
    logger.info("[account-deletion-cron] Tick started")
    try:
        stats = await AccountDeletionService.run_purge_batch()
        logger.info(
            "[account-deletion-cron] Tick finished eligible=%s purged=%s failed=%s skipped_lock=%s",
            stats.eligible,
            stats.purged,
            stats.failed,
            stats.skipped_lock,
        )
    except Exception:
        logger.exception("[account-deletion-cron] Tick failed")


async def cron_purge_deleted_accounts() -> None:
    """Background loop started from FastAPI lifespan."""
    interval_seconds = max(
        1, deletion_settings.account_deletion_cron_interval_hours
    ) * 3600
    logger.info(
        "[account-deletion-cron] Starting worker interval_hours=%s",
        deletion_settings.account_deletion_cron_interval_hours,
    )
    try:
        while True:
            await process_expired_account_deletions()
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("[account-deletion-cron] Worker cancelled")
        raise
    except Exception:
        logger.exception("[account-deletion-cron] Worker crashed")
