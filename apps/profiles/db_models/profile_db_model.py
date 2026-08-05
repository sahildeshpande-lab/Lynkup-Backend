from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, String, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from common.enums import ProfileVisibility


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Profile(SQLModel, table=True):
    __tablename__ = "profiles"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, unique=True, index=True)
    first_name: str | None = Field(default=None, sa_column=Column(String(64)))
    last_name: str | None = Field(default=None, sa_column=Column(String(64)))
    bio: str | None = Field(default=None, sa_column=Column(String(500)))
    university_id: UUID | None = Field(default=None, foreign_key="universities.id", index=True)
    profile_interests_id: list[int] | None = Field(default=None, sa_column=Column(JSON, nullable=True, server_default='[]'))
    major: str | None = Field(default=None, sa_column=Column(String(255)))
    minor: str | None = Field(default=None, sa_column=Column(String(255)))
    edu_level: str | None = Field(default=None, sa_column=Column(String(32), nullable=True, index=True))
    graduation_date: date | None = Field(default=None, sa_column=Column(Date))
    country_id: UUID | None = Field(default=None, foreign_key="countries.id", index=True)
    location_text: str | None = Field(default=None, sa_column=Column(String(255)))
    profile_visibility: ProfileVisibility = Field(default=ProfileVisibility.public, index=True)
    online_presence_visible: bool = Field(default=True, nullable=False)
    completeness_score: int = Field(default=0, nullable=False)
    posts_count: int = Field(default=0, nullable=False)
    followers_count: int = Field(default=0, nullable=False)
    following_count: int = Field(default=0, nullable=False)
    completeness_rubric_version: str = Field(default="v1", sa_column=Column(String(32), nullable=False))
    profile_photo_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    banner_photo_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    extracted_keywords: dict | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    keywords_updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    # Learning recommendation persistence (cron snapshot storage).
    learning_recommendations: dict | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    recommendations_updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class CompletenessWeight(SQLModel, table=True):
    __tablename__ = "completeness_weights"

    id: int = Field(default=1, primary_key=True)
    bio: float = Field(default=10.0)
    university: float = Field(default=10.0)
    major: float = Field(default=10.0)
    edu_level: float = Field(default=10.0)
    first_name: float = Field(default=10.0)
    last_name: float = Field(default=10.0)
    email: float = Field(default=10.0)
    profile_photo_url: float = Field(default=10.0)
    interests: float = Field(default=10.0)
    graduation_date: float = Field(default=10.0)
    location: float = Field(default=10.0)


Profile.model_rebuild()
CompletenessWeight.model_rebuild()

