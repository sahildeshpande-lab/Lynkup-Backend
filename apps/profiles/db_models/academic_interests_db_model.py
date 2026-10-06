from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)



class AcademicInterest(SQLModel, table=True):
    __tablename__ = "academic_interests"

    id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            primary_key=True,
            autoincrement=True,
        ),
    )

    name: str = Field(
        sa_column=Column(
            String(100),
            nullable=False,
        )
    )

    # TEMPORARILY nullable for deployment
    education_level_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("education_levels.id"),
            nullable=True
        ),
    )

    major_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("majors.id"), nullable=True, index=False),
    )
    minor_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("minors.id"), nullable=True, index=False),
    )
    interest_added_by: str | None = Field(
        default=None,
        sa_column=Column(String(16), nullable=True),
    )

    is_active: bool = Field(
        default=True,
        sa_column=Column(
            Boolean,
            server_default=text("true"),
            nullable=False,
            index=True,
        ),
    )

    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
    )

    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
    )

    __table_args__ = (
        CheckConstraint(
            "(major_id IS NOT NULL) OR (minor_id IS NULL)",
            name="ck_academic_interests_no_minor_only",
        ),
        CheckConstraint(
            "interest_added_by IS NULL OR interest_added_by IN ('user', 'admin')",
            name="ck_academic_interests_added_by",
        ),
        # Case-insensitive uniqueness is enforced in application code (LOWER match).
        # DB unique indexes are major-scoped on the raw ``name`` column so Alembic
        # metadata stays aligned with Postgres.
        Index(
            "uq_academic_interests_major_name_no_minor",
            "major_id",
            "name",
            unique=True,
            postgresql_where=text("major_id IS NOT NULL AND minor_id IS NULL"),
            sqlite_where=text("major_id IS NOT NULL AND minor_id IS NULL"),
        ),
        Index(
            "uq_academic_interests_major_minor_name",
            "major_id",
            "minor_id",
            "name",
            unique=True,
            postgresql_where=text("major_id IS NOT NULL AND minor_id IS NOT NULL"),
            sqlite_where=text("major_id IS NOT NULL AND minor_id IS NOT NULL"),
        ),
        Index(
            "ix_academic_interests_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index("ix_academic_interests_minor_id", "minor_id"),
    )
