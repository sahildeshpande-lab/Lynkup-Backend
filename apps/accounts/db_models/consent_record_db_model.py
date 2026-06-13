from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConsentRecord(SQLModel, table=True):
    __tablename__ = "consent_records"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    consent_type: str = Field(sa_column=Column(String(64), nullable=False, index=True))
    granted: bool = Field(default=False, nullable=False)
    ip_address: str | None = Field(default=None, sa_column=Column(String(64)))
    consented_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))

