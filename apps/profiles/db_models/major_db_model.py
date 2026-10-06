from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Major(SQLModel, table=True):
    __tablename__ = "majors"

    id: int | None = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    name: str = Field(sa_column=Column(String(255), nullable=False))
    major_added_by: str | None = Field(
        default=None,
        sa_column=Column(String(16), nullable=True),
    )
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, server_default=text("true"), nullable=False, index=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_majors_name"),
        CheckConstraint(
            "major_added_by IS NULL OR major_added_by IN ('user', 'admin')",
            name="ck_majors_added_by",
        ),
    )
