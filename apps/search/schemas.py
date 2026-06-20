from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: Any | None = None


class UniversitySearchParams(BaseModel):
    query: str
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)