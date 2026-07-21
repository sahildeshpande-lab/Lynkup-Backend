from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    status: bool = True
    message: str = "success"
    data: Any | None = None
