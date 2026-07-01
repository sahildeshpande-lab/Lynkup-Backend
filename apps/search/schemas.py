from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


from common.schemas import ApiResponse


class UniversitySearchParams(BaseModel):
    query: str
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)