from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from typing import ClassVar

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.orm import synonym
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TransactionalEmailLog(SQLModel, table=True):
    __tablename__ = "transactional_email_log"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    to_email: str = Field(sa_column=Column("to", String(320), nullable=False, index=True))
    from_email: str = Field(sa_column=Column("from", String(320), nullable=False))
    content: str = Field(sa_column=Column("body", Text, nullable=False))
    purpose: str = Field(sa_column=Column(String(128), nullable=False))
    subject: str = Field(default="", sa_column=Column(String(256), nullable=False, server_default=""))
    is_send: bool = Field(default=False, sa_column=Column("is_sent", Boolean, nullable=False, server_default="false"))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    # sent_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    # error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    to: ClassVar = synonym("to_email")
    body: ClassVar = synonym("content")
    is_sent: ClassVar = synonym("is_send")

    def __init__(self, **data):
        data.setdefault("to_email", data.pop("to", None))
        data.setdefault("content", data.pop("body", None))
        data.setdefault("is_send", data.pop("is_sent", False))
        self._attachment_val = data.pop("attachment", None)
        super().__init__(**data)

    @property
    def attachment(self) -> str | None:
        return getattr(self, "_attachment_val", None)

    @attachment.setter
    def attachment(self, value: str | None) -> None:
        self._attachment_val = value
