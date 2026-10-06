from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, or_, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.export.enums import DataExportStatus
from apps.export.models import DataExportRequest


async def claim_export(
    db: AsyncSession,
    export_id: UUID,
    lease_owner: str,
    lease_seconds: int = 1800,
) -> bool:
    """Atomically claim an export row for processing.

    Succeeds only when the row is ``queued``, or ``processing`` with an expired
    lease. Concurrent callers race on a single conditional UPDATE; PostgreSQL
    grants the row lock to one transaction, then re-evaluates the predicate.
    """
    db_now = func.now()
    stmt = (
        update(DataExportRequest)
        .where(DataExportRequest.id == export_id)
        .where(
            or_(
                DataExportRequest.status == DataExportStatus.queued,
                and_(
                    DataExportRequest.status == DataExportStatus.processing,
                    DataExportRequest.lease_expires_at < db_now,
                ),
            )
        )
        .values(
            status=DataExportStatus.processing,
            lease_owner=lease_owner,
            lease_expires_at=db_now
            + text(f"INTERVAL '{int(lease_seconds)} seconds'"),
            attempt_count=DataExportRequest.attempt_count + 1,
            started_at=func.coalesce(DataExportRequest.started_at, db_now),
            updated_at=db_now,
        )
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    return result.rowcount == 1
