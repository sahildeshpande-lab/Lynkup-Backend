from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Integer, String, Text, Enum
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship

from common.enums import PostState


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Post(SQLModel, table=True):
    __tablename__ = "posts"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    author_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    state: PostState = Field(
        sa_column=Column(Enum(PostState, name="poststate"), nullable=False, default=PostState.draft, index=True),
    )
    content_html: str | None = Field(default=None, sa_column=Column(Text))
    caption: str | None = Field(default=None, sa_column=Column(String(255)))
    revision_number: int = Field(default=1, sa_column=Column(Integer, nullable=False, default=1))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    attachments: List["PostAttachment"] = Relationship(
        sa_relationship=relationship("PostAttachment", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )
    revisions: List["PostRevision"] = Relationship(
        sa_relationship=relationship("PostRevision", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )
    hashtags: List["PostHashtag"] = Relationship(
        sa_relationship=relationship("PostHashtag", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )
    topics: List["PostTopic"] = Relationship(
        sa_relationship=relationship("PostTopic", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )
    reactions: List["PostReaction"] = Relationship(
        sa_relationship=relationship("PostReaction", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )
    link_previews: List["LinkPreview"] = Relationship(
        sa_relationship=relationship("LinkPreview", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )
