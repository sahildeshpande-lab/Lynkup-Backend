from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, text
from sqlmodel import Field, SQLModel

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class AcademicInterest(SQLModel, table=True):
    __tablename__ = "academic_interests"

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    name: str = Field(sa_column=Column(String(100), nullable=False, unique=True))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, server_default=text("true"), nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")))
