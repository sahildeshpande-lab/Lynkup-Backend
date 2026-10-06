from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminSigningKeyStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class AdminSigningKey(SQLModel, table=True):
    """Browser RSA public keys used to sign Web Admin API requests.

    Bound to both user_id and an independent admin session_id.
    The private key never leaves the browser. JWT expiry is independent of
    this key's lifetime — the same active key can sign requests across many
    access-token refreshes until the session/key is revoked.
    """

    __tablename__ = "admin_signing_keys"
    __table_args__ = (UniqueConstraint("key_id", name="uq_admin_signing_keys_key_id"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    key_id: UUID = Field(default_factory=uuid4, index=True, nullable=False)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    session_id: UUID = Field(foreign_key="admin_sessions.id", nullable=False, index=True)
    public_key: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(
        default=AdminSigningKeyStatus.ACTIVE.value,
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
    last_used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


AdminSigningKey.model_rebuild()
