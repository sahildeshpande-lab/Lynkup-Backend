from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, String
from sqlmodel import Field, SQLModel

from common.enums import ProfileVisibility


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Profile(SQLModel, table=True):
    __tablename__ = "profiles"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, unique=True, index=True)
    display_name: str | None = Field(default=None, sa_column=Column(String(128)))
    bio: str | None = Field(default=None, sa_column=Column(String(500)))
    university_id: UUID | None = Field(default=None, foreign_key="universities.id", index=True)
    academic_program_id: UUID | None = Field(default=None, foreign_key="academic_programs.id", index=True)
    major: str | None = Field(default=None, sa_column=Column(String(255)))
    minor: str | None = Field(default=None, sa_column=Column(String(255)))
    edu_level: str | None = Field(default=None, sa_column=Column(String(32), nullable=True, index=True))
    graduation_date: date | None = Field(default=None, sa_column=Column(Date))
    country_id: UUID | None = Field(default=None, foreign_key="countries.id", index=True)
    location_text: str | None = Field(default=None, sa_column=Column(String(255)))
    profile_visibility: ProfileVisibility = Field(default=ProfileVisibility.public, index=True)
    online_presence_visible: bool = Field(default=True, nullable=False)
    completeness_score: int = Field(default=0, nullable=False)
    completeness_rubric_version: str = Field(default="v1", sa_column=Column(String(32), nullable=False))
    profile_photo_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    banner_photo_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    welcome_message: str | None = Field(default=None, sa_column=Column(String(255)))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))

