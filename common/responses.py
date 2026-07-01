from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from .schemas import ApiResponse

ResponseT = TypeVar("ResponseT", bound=BaseModel)


def success_response(
    message: str = "Operation completed successfully",
    data: Any | None = None,
    *,
    response_cls: type[ResponseT] = ApiResponse,
) -> ResponseT:
    return response_cls(status=True, message=message, data=data)


def error_response(
    message: str,
    *,
    response_cls: type[ResponseT] = ApiResponse,
) -> ResponseT:
    return response_cls(status=False, message=message, data=None)
