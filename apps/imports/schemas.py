from __future__ import annotations

from pydantic import BaseModel, Field

from apps.imports.enums import ImportType


class ImportRowIssue(BaseModel):
    row: int
    reason: str


class ImportResultData(BaseModel):
    type: ImportType
    fileName: str
    totalRecords: int
    successfulCount: int
    duplicateCount: int
    failedCount: int
    failedRows: list[ImportRowIssue] = Field(default_factory=list)
    duplicateRows: list[ImportRowIssue] = Field(default_factory=list)
