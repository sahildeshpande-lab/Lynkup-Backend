from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ShareEvent(SQLModel, table=True):
    __tablename__ = "share_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    post_id: UUID = Field(foreign_key="posts.id", nullable=False)
    branch_code: str | None = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    branch_url: str | None = Field(
        default=None,
        sa_column=Column(String(2048), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            onupdate=utc_now,
        ),
    )

    user: "User" = Relationship(
        sa_relationship=relationship("User", back_populates="share_events")
    )
    post: "Post" = Relationship(
        sa_relationship=relationship("Post", back_populates="share_events")
    )

    __table_args__ = (
        Index("ix_share_events_user_id", "user_id"),
        Index("ix_share_events_post_id", "post_id"),
        Index("ix_share_events_user_id_post_id", "user_id", "post_id"),
        Index("ix_share_events_branch_code", "branch_code"),
        UniqueConstraint("user_id", "post_id", name="uq_share_events_user_post"),
    )
