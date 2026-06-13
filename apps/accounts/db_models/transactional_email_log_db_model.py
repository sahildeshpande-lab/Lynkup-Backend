from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TransactionalEmailLog(SQLModel, table=True):
    __tablename__ = "transactional_email_log"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    to: str = Field(sa_column=Column(String(320), nullable=False, index=True))
    from_email: str = Field(sa_column=Column("from", String(320), nullable=False))
    body: str = Field(sa_column=Column(Text, nullable=False))
    attachment: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    purpose: str = Field(sa_column=Column(String(128), nullable=False))
    subject: str = Field(default="", sa_column=Column(String(256), nullable=False, server_default=""))
    is_sent: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="false"))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
