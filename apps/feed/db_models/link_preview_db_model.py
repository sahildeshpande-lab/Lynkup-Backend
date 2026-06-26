from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, String, DateTime
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LinkPreview(SQLModel, table=True):
    __tablename__ = "link_previews"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    post_id: UUID = Field(foreign_key="posts.id", nullable=False, index=True)
    url: str = Field(sa_column=Column(String(2048), nullable=False))
    title: str | None = Field(default=None, sa_column=Column(String(512)))
    description: str | None = Field(default=None, sa_column=Column(String(1024)))
    image_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    post: "Post" = Relationship(
        sa_relationship=relationship("Post", back_populates="link_previews")
    )
