from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Column, ForeignKey, Index, String
from sqlmodel import Field, SQLModel


class ProfileInterest(SQLModel, table=True):
    __tablename__ = "profile_interests"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    profile_id: UUID = Field(foreign_key="profiles.id", nullable=False, index=True)
    interest_tag: str = Field(sa_column=Column(String(128), nullable=False, index=True))

    __table_args__ = (
        Index("ix_profile_interests_profile_id_interest_tag", "profile_id", "interest_tag", unique=True),
    )

