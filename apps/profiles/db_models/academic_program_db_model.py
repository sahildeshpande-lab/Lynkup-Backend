from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Column, ForeignKey, Index, String
from sqlmodel import Field, SQLModel


class AcademicProgram(SQLModel, table=True):
    __tablename__ = "academic_programs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    university_id: UUID = Field(foreign_key="universities.id", nullable=False, index=True)
    name: str = Field(sa_column=Column(String(255), nullable=False, index=True))
    code: str | None = Field(default=None, sa_column=Column(String(64)))

