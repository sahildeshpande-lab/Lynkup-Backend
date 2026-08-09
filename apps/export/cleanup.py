from __future__ import annotations

import logging

from apps.export.service import DataExportService, get_data_export_service
from core.database.session import async_session_factory

logger = logging.getLogger(__name__)


async def cleanup_expired_exports(
    service: DataExportService | None = None,
) -> int:
    """Delete expired export ZIP files and mark records as expired.

    Intended to be invoked by Linux Cron or another external scheduler.
    Do not call this from an infinite FastAPI-managed loop.
    """
    svc = service or get_data_export_service()
    async with async_session_factory() as db:
        cleaned = await svc.cleanup_expired_exports(db=db)
    logger.info("Expired export cleanup finished; cleaned=%s", cleaned)
    return cleaned
