from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from common.pagination import PaginationParams
from core.db.session import get_session
from .schemas import ApiResponse, UniversitySearchParams
from . import services

router = APIRouter(tags=["3] Search & Discovery"])


@router.get("/universities", response_model=ApiResponse)
async def universities(
    query: str = Query(...),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    if not query or len(query.strip()) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query must be at least 3 characters",
        )
    data = await services.search_universities(
        UniversitySearchParams(query=query.strip(), page=pagination.page, pageSize=pagination.pageSize),
        db,
    )
    return ApiResponse(message="Universities fetched successfully", data=data)
