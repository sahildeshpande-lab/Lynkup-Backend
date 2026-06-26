from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Column, DateTime
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostHashtag(SQLModel, table=True):
    __tablename__ = "post_hashtags"

    post_id: UUID = Field(foreign_key="posts.id", primary_key=True, nullable=False, index=True)
    hashtag_id: UUID = Field(foreign_key="hashtags.id", primary_key=True, nullable=False, index=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    post: "Post" = Relationship(
        sa_relationship=relationship("Post", back_populates="hashtags")
    )
    hashtag: "Hashtag" = Relationship(
        sa_relationship=relationship("Hashtag", back_populates="posts")
    )
