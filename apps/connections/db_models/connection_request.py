from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String, text, UniqueConstraint, CheckConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConnectionRequest(SQLModel, table=True):
    __tablename__ = "connection_requests"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    sender_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    receiver_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    status: str = Field(sa_column=Column(String(20), default="pending", nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=utc_now)
    )

    __table_args__ = (
        UniqueConstraint("sender_user_id", "receiver_user_id", "status", name="uq_connection_requests_sender_receiver_status"),
        CheckConstraint("sender_user_id != receiver_user_id", name="chk_connection_request_not_self"),
    )
