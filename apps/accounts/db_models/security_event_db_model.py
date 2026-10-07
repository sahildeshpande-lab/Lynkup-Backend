from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4
from enum import Enum

from sqlalchemy import Column, DateTime, String, JSON
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SecurityEventType(str, Enum):
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
    ROLE_CHANGED = "ROLE_CHANGED"
    TOKEN_REVOKED = "TOKEN_REVOKED"  # nosec B105 -- enum value, not a password
    EMAIL_CHANGED = "EMAIL_CHANGED"
    KEY_REGISTERED = "KEY_REGISTERED"
    KEY_REVOKED = "KEY_REVOKED"
    SIGNATURE_VERIFICATION_FAILED = "SIGNATURE_VERIFICATION_FAILED"
    TIMESTAMP_VALIDATION_FAILED = "TIMESTAMP_VALIDATION_FAILED"
    NONCE_REPLAY_DETECTED = "NONCE_REPLAY_DETECTED"
    SESSION_REVOKED = "SESSION_REVOKED"
    UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"

    # Mobile / app security (additive — do not reuse admin RSA event meanings).
    MOBILE_DEVICE_VERIFIED = "MOBILE_DEVICE_VERIFIED"
    MOBILE_REQUEST_VERIFIED = "MOBILE_REQUEST_VERIFIED"
    ANDROID_INTEGRITY_VERIFIED = "ANDROID_INTEGRITY_VERIFIED"
    IOS_ATTEST_VERIFIED = "IOS_ATTEST_VERIFIED"
    MOBILE_MISSING_DEVICE = "MOBILE_MISSING_DEVICE"
    MOBILE_UNKNOWN_DEVICE = "MOBILE_UNKNOWN_DEVICE"
    MOBILE_INACTIVE_DEVICE = "MOBILE_INACTIVE_DEVICE"
    MOBILE_TIMESTAMP_INVALID = "MOBILE_TIMESTAMP_INVALID"
    MOBILE_NONCE_REPLAY = "MOBILE_NONCE_REPLAY"
    MOBILE_REQUEST_PROOF_INVALID = "MOBILE_REQUEST_PROOF_INVALID"
    MOBILE_RATE_LIMIT_EXCEEDED = "MOBILE_RATE_LIMIT_EXCEEDED"
    ANDROID_INTEGRITY_FAILED = "ANDROID_INTEGRITY_FAILED"
    IOS_ATTEST_FAILED = "IOS_ATTEST_FAILED"
    IOS_ASSERTION_REPLAY = "IOS_ASSERTION_REPLAY"
    MOBILE_SECURITY_REDIS_UNAVAILABLE = "MOBILE_SECURITY_REDIS_UNAVAILABLE"


class SecurityEvent(SQLModel, table=True):
    __tablename__ = "security_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    event_type: SecurityEventType = Field(sa_column=Column(String(64), nullable=False, index=True))
    ip_address: str | None = Field(default=None, sa_column=Column(String(64)))
    event_metadata: dict | None = Field(default=None, sa_column=Column("metadata", JSON))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
