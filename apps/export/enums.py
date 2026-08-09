from __future__ import annotations

from enum import Enum


class DataExportStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    expired = "expired"
