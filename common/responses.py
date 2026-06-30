from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from apps.accounts.schemas import ApiResponse

ResponseT = TypeVar("ResponseT", bound=BaseModel)


def success_response(
    message: str = "success",
    data: Any | None = None,
    *,
    response_cls: type[ResponseT] = ApiResponse,
    **extra: Any,
) -> ResponseT:
    return response_cls(status=True, message=message, data=data, **extra)


def error_response(
    message: str,
    data: Any | None = None,
    *,
    response_cls: type[ResponseT] = ApiResponse,
    **extra: Any,
) -> ResponseT:
    return response_cls(status=False, message=message, data=data, **extra)
