from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, Uuid, text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Template(SQLModel, table=True):
    """Database model for dynamic email templates."""

    __tablename__ = "templates"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(
        sa_column=Column(String(100), nullable=False, unique=True, index=True)
    )
    subject: str = Field(sa_column=Column(String(255), nullable=False))
    body_html: str = Field(sa_column=Column(Text, nullable=False))
    updated_by: UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid(),
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    status: str = Field(
        default="active",
        sa_column=Column(
            String(20),
            nullable=False,
            server_default=text("'active'"),
        ),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            onupdate=utc_now,
        ),
    )
