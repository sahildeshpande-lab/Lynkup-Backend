from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Integer, Enum, Boolean
from sqlalchemy.dialects.postgresql import JSONB
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
    content: dict | None = Field(default_factory=dict, sa_column=Column(JSONB, nullable=True, default={}))
    revision_number: int = Field(default=1, sa_column=Column(Integer, nullable=False, default=1))
    is_edited: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    
    is_moderator_reviewed: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    moderator_id: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        nullable=True,
        index=True,
    )
    reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    like_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    repost_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    share_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    comment_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))    
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
    bookmarks: List["Bookmark"] = Relationship(
        sa_relationship=relationship("Bookmark", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )
    share_events: List["ShareEvent"] = Relationship(
        sa_relationship=relationship("ShareEvent", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )
    comments: List["Comment"] = Relationship(
        sa_relationship=relationship("Comment", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )
    link_previews: List["LinkPreview"] = Relationship(
        sa_relationship=relationship("LinkPreview", back_populates="post", cascade="all, delete-orphan", lazy="selectin")
    )

    # ---- Convenience properties for backward-compatible access ----

    @property
    def caption(self) -> str | None:
        """Read caption from the content JSONB."""
        if self.content:
            return self.content.get("caption")
        return None

    @property
    def content_html(self) -> str | None:
        """Read content_html from the content JSONB."""
        if self.content:
            return self.content.get("content_html")
        return None

    @property
    def visibility(self) -> str:
        """Read visibility from the content JSONB, defaulting to 'public'."""
        if self.content:
            return self.content.get("visibility", "public")
        return "public"
