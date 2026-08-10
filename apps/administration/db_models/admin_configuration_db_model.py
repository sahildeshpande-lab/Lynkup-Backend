from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminConfiguration(SQLModel, table=True):
    __tablename__ = "admin_configurations"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    key: str = Field(
        sa_column=Column(String(100), nullable=False, unique=True, index=True)
    )
    name: str = Field(sa_column=Column(String(255), nullable=False))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    is_enabled: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default="true")
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


AdminConfiguration.model_rebuild()
