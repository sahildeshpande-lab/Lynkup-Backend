from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminSessionStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class AdminSession(SQLModel, table=True):
    """Independent Web Admin browser/session identifier.

    Distinct from user_id, access JWT, and refresh token values.
    Created at admin login; preserved across access-token refresh.
    Ends on logout or password change/reset (status → revoked).
    """

    __tablename__ = "admin_sessions"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    status: str = Field(
        default=AdminSessionStatus.ACTIVE.value,
        sa_column=Column(String(20), nullable=False, index=True, server_default="active"),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_seen_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


AdminSession.model_rebuild()
