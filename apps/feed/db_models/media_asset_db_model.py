from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String, Integer
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship

from common.enums import MediaAssetState, MediaType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MediaAsset(SQLModel, table=True):
    __tablename__ = "media_assets"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    owner_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    key: str = Field(sa_column=Column(String(255), nullable=False, unique=True))
    type: MediaType = Field(
        sa_column=Column(String(20), nullable=False, index=True),
    )
    original_filename: str | None = Field(default=None, sa_column=Column(String(255)))
    mime_type: str | None = Field(default=None, sa_column=Column(String(100)))
    file_size: int | None = Field(default=None, sa_column=Column(Integer))
    state: MediaAssetState = Field(
        default=MediaAssetState.draft,
        sa_column=Column(String(20), nullable=False, default="draft"),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    posts: List["PostAttachment"] = Relationship(
        sa_relationship=relationship("PostAttachment", back_populates="media_asset", lazy="selectin")
    )
