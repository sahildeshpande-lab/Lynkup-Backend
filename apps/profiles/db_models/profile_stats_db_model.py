from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Column, Integer
from sqlmodel import Field, SQLModel


class ProfileStats(SQLModel, table=True):
    __tablename__ = "profile_stats"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    profile_id: UUID = Field(foreign_key="profiles.id", nullable=False, unique=True, index=True)
    connection_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
