from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship


class PostAttachment(SQLModel, table=True):
    __tablename__ = "post_attachments"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    post_id: UUID = Field(foreign_key="posts.id", nullable=False, index=True)
    media_asset_id: UUID = Field(foreign_key="media_assets.id", nullable=False, index=True)

    post: "Post" = Relationship(
        sa_relationship=relationship("Post", back_populates="attachments")
    )
    media_asset: "MediaAsset" = Relationship(
        sa_relationship=relationship("MediaAsset", back_populates="posts", lazy="selectin")
    )
