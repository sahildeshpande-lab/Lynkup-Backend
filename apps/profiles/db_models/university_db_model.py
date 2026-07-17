from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Column, ForeignKey, Index, JSON, String
from sqlmodel import Field, SQLModel


class University(SQLModel, table=True):
    __tablename__ = "universities"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    slug: str = Field(sa_column=Column(String(255), nullable=False, unique=True, index=True))
    country_id: UUID = Field(foreign_key="countries.id", nullable=False, index=True)
    website: str | None = Field(default=None, sa_column=Column(String(2048), nullable=True))
    major: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    minor: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    academic_program: list[dict] | None = Field(default=None, sa_column=Column(JSON))
    is_active: bool = Field(default=True, nullable=False, index=True)

    __table_args__ = (
        Index("ix_universities_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
    )
