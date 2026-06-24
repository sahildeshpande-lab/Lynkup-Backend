from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text, UniqueConstraint, CheckConstraint, Boolean
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Block(SQLModel, table=True):
    __tablename__ = "blocks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    blocker_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    blocked_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    is_active: bool = Field(default=True, sa_column=Column(Boolean, default=True, nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=utc_now)
    )

    __table_args__ = (
        UniqueConstraint("blocker_user_id", "blocked_user_id", name="uq_blocks_blocker_blocked"),
        CheckConstraint("blocker_user_id != blocked_user_id", name="chk_blocks_not_self"),
    )
