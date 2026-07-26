from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text, text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserInstallation(SQLModel, table=True):
    __tablename__ = "user_installations"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    platform: str | None = Field(default=None, sa_column=Column(String(20), nullable=True, index=True))
    fcm_token: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    device_id: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    app_version: str | None = Field(default=None, sa_column=Column(String(64)))
    installed_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    last_active_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))

    __table_args__ = (
        Index("ix_user_installations_user_id_device_id", "user_id", "device_id", unique=True),
    )

