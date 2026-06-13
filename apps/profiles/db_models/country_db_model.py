from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Column, Index, String
from sqlmodel import Field, SQLModel


class Country(SQLModel, table=True):
    __tablename__ = "countries"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    name: str = Field(sa_column=Column(String(128), nullable=False, index=True))
    iso_code: str = Field(sa_column=Column(String(2), nullable=False, unique=True, index=True))

