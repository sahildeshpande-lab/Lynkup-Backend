from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, String, Text, text
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserInstallation(SQLModel, table=True):
    __tablename__ = "user_installations"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    platform: str | None = Field(default=None, sa_column=Column(String(20), nullable=True, index=True))
    fcm_token: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    device_id: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    app_version: str | None = Field(default=None, sa_column=Column(String(64)))
    installed_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    last_active_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true")))
    # OTP / email device trust — do NOT overload with Play Integrity / App Attest.
    is_device_verified: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    verified_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    # Per-device HMAC key (server-issued at enrollment). Not a global app secret.
    # Stored as URL-safe base64; never log this value.
    mobile_hmac_secret: str | None = Field(default=None, sa_column=Column(Text, nullable=True))

    # Android Play Integrity state (nullable / additive).
    android_package_name: str | None = Field(default=None, sa_column=Column(String(255), nullable=True))
    android_certificate_digest: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    android_integrity_level: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    android_last_verified_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    # iOS App Attest state (nullable / additive). Never store the private key.
    app_attest_key_id: str | None = Field(default=None, sa_column=Column(String(128), nullable=True, index=True))
    app_attest_public_key: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    app_attest_environment: str | None = Field(default=None, sa_column=Column(String(32), nullable=True))
    app_attest_counter: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    app_attest_last_verified_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    __table_args__ = (
        Index("ix_user_installations_user_id_device_id", "user_id", "device_id", unique=True),
        Index("ix_user_installations_app_attest_key_id", "app_attest_key_id"),
    )
