from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Column, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship

from common.enums import ReactionType


class PostReaction(SQLModel, table=True):
    __tablename__ = "post_reactions"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    post_id: UUID = Field(foreign_key="posts.id", nullable=False, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    reaction_type: ReactionType = Field(
        sa_column=Column(String(20), nullable=False),
    )

    post: "Post" = Relationship(
        sa_relationship=relationship("Post", back_populates="reactions")
    )

    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_post_reactions_post_user"),
    )
