from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, JSON
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostRevision(SQLModel, table=True):
    __tablename__ = "post_revisions"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    post_id: UUID = Field(foreign_key="posts.id", nullable=False, index=True)
    editor_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    content: dict | None = Field(default=None, sa_column=Column(JSON))
    media: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    triggered_moderation_review: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    post: "Post" = Relationship(
        sa_relationship=relationship("Post", back_populates="revisions")
    )
