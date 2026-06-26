from __future__ import annotations

from typing import List
from uuid import UUID, uuid4

from sqlalchemy import Column, String
from sqlalchemy.orm import relationship
from sqlmodel import Field, SQLModel, Relationship


class Topic(SQLModel, table=True):
    __tablename__ = "topics"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str = Field(sa_column=Column(String(255), nullable=False, unique=True, index=True))

    posts: List["PostTopic"] = Relationship(
        sa_relationship=relationship("PostTopic", back_populates="topic", cascade="all, delete-orphan", lazy="selectin")
    )
