from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


from common.schemas import ApiResponse


class UniversitySearchParams(BaseModel):
    query: str
    page: int | None = Field(default=None, ge=1)
    pageSize: int | None = Field(default=None, ge=1, le=200)
    sort: str | None = None
    order: str | None = None
    sort_by: str | None = None
    order_by: str | None = None





class AcademicInterestCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    educationLevelId: int = Field(ge=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Academic interest name cannot be empty")
        return normalized


class PostSearchListData(BaseModel):
    items: list[Any]
    page: int
    pageSize: int
    totalItems: int
    totalPages: int


class PostSearchResponse(ApiResponse):
    data: PostSearchListData | None = None