from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Enum, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from common.enums import AdminConfigurationType


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminConfiguration(SQLModel, table=True):
    __tablename__ = "admin_configurations"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    key: str = Field(
        sa_column=Column(String(100), nullable=False, unique=True, index=True)
    )
    name: str = Field(sa_column=Column(String(255), nullable=False))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    configuration_type: AdminConfigurationType = Field(
        default=AdminConfigurationType.FEATURE_FLAG,
        sa_column=Column(
            Enum(
                AdminConfigurationType,
                name="adminconfigurationtype",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
            server_default=text("'feature_flag'"),
            index=True,
        ),
    )
    value: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    is_enabled: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default="true")
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


AdminConfiguration.model_rebuild()
