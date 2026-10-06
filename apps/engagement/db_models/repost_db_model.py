from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Index, UniqueConstraint, desc, text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Repost(SQLModel, table=True):
    __tablename__ = "reposts"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    profile_id: UUID = Field(foreign_key="profiles.id", nullable=False)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    post_id: UUID = Field(foreign_key="posts.id", nullable=False)
    is_deleted: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    __table_args__ = (
        Index("ix_reposts_profile_id", "profile_id"),
        Index("ix_reposts_user_id", "user_id"),
        Index("ix_reposts_post_id", "post_id"),
        Index("ix_reposts_profile_id_post_id", "profile_id", "post_id"),
        Index(
            "ix_reposts_user_active_created",
            "user_id",
            desc("created_at"),
            postgresql_where=text("is_deleted = false"),
        ),
        UniqueConstraint("profile_id", "post_id", name="uq_reposts_profile_post"),
        UniqueConstraint("user_id", "post_id", name="uq_reposts_user_post"),
    )
