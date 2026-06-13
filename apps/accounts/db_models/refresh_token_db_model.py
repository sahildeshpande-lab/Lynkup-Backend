from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Index, String
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    token_hash: str = Field(sa_column=Column(String(255), nullable=False))
    device_id: str | None = Field(default=None, sa_column=Column(String(255), nullable=True))
    device_name: str | None = Field(default=None, sa_column=Column(String(255), nullable=True))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    last_used_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))

    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_token_hash", "token_hash"),
    )
