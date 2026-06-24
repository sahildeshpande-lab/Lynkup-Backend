from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text, UniqueConstraint, CheckConstraint, Boolean
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Connection(SQLModel, table=True):
    __tablename__ = "connections"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_low_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    user_high_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    is_active: bool = Field(default=True, sa_column=Column(Boolean, default=True, nullable=False))
    connected_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=utc_now)
    )

    __table_args__ = (
        UniqueConstraint("user_low_id", "user_high_id", name="uq_connections_user_low_high"),
        CheckConstraint("user_low_id < user_high_id", name="chk_connections_low_high_order"),
    )
