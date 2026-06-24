from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text, UniqueConstraint, CheckConstraint, Boolean
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Follow(SQLModel, table=True):
    __tablename__ = "follows"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    follower_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    following_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
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
        UniqueConstraint("follower_user_id", "following_user_id", name="uq_follows_follower_following"),
        CheckConstraint("follower_user_id != following_user_id", name="chk_follows_not_self"),
    )
