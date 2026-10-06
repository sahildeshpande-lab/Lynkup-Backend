from __future__ import annotations

import logging

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
