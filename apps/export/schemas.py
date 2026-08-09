from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from apps.export.enums import DataExportStatus


class ExportRequestAcceptedData(BaseModel):
    export_id: UUID
    status: DataExportStatus


class ExportStatusData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: DataExportStatus
    requested_at: datetime
    completed_at: datetime | None = None
    download_expires_at: datetime | None = None
    file_size_bytes: int | None = None
