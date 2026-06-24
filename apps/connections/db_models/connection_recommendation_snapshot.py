from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, text, JSON, DECIMAL
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConnectionRecommendationSnapshot(SQLModel, table=True):
    __tablename__ = "connection_recommendation_snapshots"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    recommended_user_ids: list[str] = Field(sa_column=Column(JSON, default=list, nullable=False))
    score: float = Field(sa_column=Column(DECIMAL, nullable=False, default=0.0))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=utc_now)
    )
