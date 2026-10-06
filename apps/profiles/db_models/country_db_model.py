from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, String, text
from sqlmodel import Field, SQLModel


class Country(SQLModel, table=True):
    __tablename__ = "countries"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str = Field(sa_column=Column(String(128), nullable=False, index=True))
    iso_code: str = Field(sa_column=Column(String(2), nullable=False, unique=True, index=True))
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, server_default=text("true"), nullable=False, index=True),
    )
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
            index=True,
        )
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
            onupdate=text("now()"),
        )
    )
