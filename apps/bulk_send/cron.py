from __future__ import annotations

import logging

from apps.bulk_send.delivery_service import process_pending_bulk_deliveries
from apps.bulk_send.enums import SENDGRID_MAX_PERSONALIZATIONS

logger = logging.getLogger(__name__)

BULK_EMAIL_CRON_INTERVAL_SECONDS = 60


async def process_bulk_emails(*, session_factory=None) -> None:
    """One scheduler tick: claim and send pending bulk campaign deliveries."""
    logger.info(
        "[bulk-email] Tick started claim_limit=%s",
        SENDGRID_MAX_PERSONALIZATIONS,
    )
    try:
        await process_pending_bulk_deliveries(
            limit=SENDGRID_MAX_PERSONALIZATIONS,
            session_factory=session_factory,
        )
    except Exception:
        logger.exception("Bulk email cron iteration failed")
        raise


async def cron_send_bulk_emails() -> None:
    """Backward-compatible alias for ``process_bulk_emails``."""
    await process_bulk_emails()
