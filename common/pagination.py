from __future__ import annotations

from math import ceil
from typing import Generic, Sequence, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=200)


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    page: int
    pageSize: int
    totalItems: int
    totalPages: int


def build_paginated_response(
    items: Sequence[T],
    page: int,
    page_size: int,
    total_items: int,
) -> PaginatedResponse[T]:
    total_pages = ceil(total_items / page_size) if total_items else 0
    return PaginatedResponse[T](
        items=list(items),
        page=page,
        pageSize=page_size,
        totalItems=total_items,
        totalPages=total_pages,
    )


def paginate_items(items: Sequence[T], page: int = 1, page_size: int = 20) -> PaginatedResponse[T]:
    start = (page - 1) * page_size
    end = start + page_size
    total_items = len(items)
    return build_paginated_response(items[start:end], page, page_size, total_items)
