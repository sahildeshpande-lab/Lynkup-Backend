from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Enum, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship

from common.enums import ReactionType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostReaction(SQLModel, table=True):
    __tablename__ = "post_reactions"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    post_id: UUID = Field(foreign_key="posts.id", nullable=False)
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

    post: "Post" = Relationship(
        sa_relationship=relationship("Post", back_populates="reactions")
    )

    __table_args__ = (
        Index("ix_post_reactions_post_id", "post_id"),
        Index("ix_post_reactions_user_id", "user_id"),
        Index("ix_post_reactions_post_id_user_id", "post_id", "user_id"),
        UniqueConstraint("post_id", "user_id", name="uq_post_reactions_post_user"),
    )
