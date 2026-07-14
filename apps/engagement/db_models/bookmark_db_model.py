from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Bookmark(SQLModel, table=True):
    __tablename__ = "bookmarks"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    post_id: UUID = Field(foreign_key="posts.id", nullable=False)
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
        sa_relationship=relationship("User", back_populates="bookmarks")
    )
    post: "Post" = Relationship(
        sa_relationship=relationship("Post", back_populates="bookmarks")
    )

    __table_args__ = (
        Index("ix_bookmarks_user_id", "user_id"),
        Index("ix_bookmarks_post_id", "post_id"),
        UniqueConstraint("user_id", "post_id", name="uq_bookmarks_user_post"),
    )
