from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Enum, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship

from common.enums import ReactionType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CommentReaction(SQLModel, table=True):
    __tablename__ = "comment_reactions"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    comment_id: UUID = Field(foreign_key="comments.id", nullable=False)
    user_id: UUID = Field(foreign_key="users.id", nullable=False)
    reaction_type: ReactionType = Field(
        sa_column=Column(Enum(ReactionType, name="reactiontype"), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    comment: "Comment" = Relationship(
        sa_relationship=relationship("Comment", back_populates="reactions")
    )
    user: "User" = Relationship(
        sa_relationship=relationship("User", back_populates="comment_reactions")
    )

    __table_args__ = (
        Index("ix_comment_reactions_comment_id", "comment_id"),
        Index("ix_comment_reactions_user_id", "user_id"),
        UniqueConstraint("comment_id", "user_id", name="uq_comment_reactions_comment_user"),
    )
