from __future__ import annotations

import logging

from apps.export.service import DataExportService, get_data_export_service
from core.database.session import async_session_factory

logger = logging.getLogger(__name__)


async def cleanup_expired_exports(
    service: DataExportService | None = None,
    *, session_factory=None,
) -> int:
    """Delete expired export ZIP files and mark records as expired.

    Invoked by Celery with a worker-owned session factory.
    """
    svc = service or get_data_export_service()
    factory = session_factory or async_session_factory
    async with factory() as db:
        cleaned = await svc.cleanup_expired_exports(db=db)
    logger.info("Expired export cleanup finished; cleaned=%s", cleaned)
    return cleaned
