from __future__ import annotations

from math import ceil
from typing import Generic, Sequence, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=200)


class OptionalPaginationParams(BaseModel):
    """Pagination that only applies when both ``page`` and ``pageSize`` are sent."""

    page: int | None = Field(default=None, ge=1)
    pageSize: int | None = Field(default=None, ge=1, le=200)


class OptionalPageSizeParams(BaseModel):
    """``page`` defaults to 1. Omit ``pageSize`` to retrieve all items."""

    page: int = Field(default=1, ge=1, description="Page number.")
    pageSize: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="Items per page. Omit to retrieve all.",
    )


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
    page_size = max(page_size or 1, 1)
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


def paginate_or_all(
    items: Sequence[T],
    page: int | None = None,
    page_size: int | None = None,
    *,
    total_items: int | None = None,
) -> PaginatedResponse[T]:
    """Paginate when both ``page`` and ``page_size`` are provided; otherwise return all items."""
    if page is not None and page_size is not None:
        if total_items is None:
            return paginate_items(items, page=page, page_size=page_size)
        return build_paginated_response(items, page, page_size, total_items)

    full_count = total_items if total_items is not None else len(items)
    return build_paginated_response(
        items,
        page=1,
        page_size=full_count if full_count > 0 else 1,
        total_items=full_count,
    )
