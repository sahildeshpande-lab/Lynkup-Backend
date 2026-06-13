from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserIdentity(SQLModel, table=True):
    __tablename__ = "user_identities"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    provider: str = Field(sa_column=Column(String(32), nullable=False, index=True))
    provider_uid: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    linked_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))

    __table_args__ = (
        Index("ix_user_identities_provider_provider_uid", "provider", "provider_uid", unique=True),
    )

