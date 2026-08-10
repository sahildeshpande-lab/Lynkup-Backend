from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Index, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Comment(SQLModel, table=True):
    __tablename__ = "comments"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    post_id: UUID = Field(foreign_key="posts.id", nullable=False)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    parent_comment_id: UUID | None = Field(
        default=None,
        foreign_key="comments.id",
        nullable=True,
    )
    level: int = Field(
        default=1,
        sa_column=Column(SmallInteger, nullable=False, server_default="1"),
    )
    is_deleted: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="false"),
    )
    like_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    reply_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    comment_text: str = Field(sa_column=Column(Text, nullable=False))
    auto_moderation_scanned_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    moderation_words_found: list[str] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    post: "Post" = Relationship(
        sa_relationship=relationship("Post", back_populates="comments")
    )
    author: "User" = Relationship(
        sa_relationship=relationship("User", back_populates="comments")
    )
    parent_comment: "Comment | None" = Relationship(
        sa_relationship=relationship(
            "Comment",
            back_populates="replies",
            remote_side="Comment.id",
        )
    )
    replies: List["Comment"] = Relationship(
        sa_relationship=relationship(
            "Comment",
            back_populates="parent_comment",
        )
    )
    reactions: List["CommentReaction"] = Relationship(
        sa_relationship=relationship(
            "CommentReaction",
            back_populates="comment",
            cascade="all, delete-orphan",
            lazy="selectin",
        )
    )

    __table_args__ = (
        CheckConstraint("level >= 1 AND level <= 3", name="ck_comments_level_range"),
        Index("ix_comments_post_id", "post_id"),
        Index("ix_comments_parent_comment_id", "parent_comment_id"),
        Index("ix_comments_user_id", "user_id"),
    )
