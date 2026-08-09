from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String, Text, BigInteger
from sqlmodel import Field, SQLModel

from apps.export.enums import DataExportStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DataExportRequest(SQLModel, table=True):
    __tablename__ = "data_export_requests"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    status: DataExportStatus = Field(
        default=DataExportStatus.queued,
        sa_column=Column(String(32), nullable=False, index=True),
    )
    requested_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    storage_key: str | None = Field(
        default=None,
        sa_column=Column(String(512), nullable=True),
    )
    download_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    file_size_bytes: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    error_message: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
