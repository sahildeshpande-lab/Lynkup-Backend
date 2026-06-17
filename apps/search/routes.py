from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status 
from sqlalchemy.ext.asyncio import AsyncSession

from common.pagination import PaginationParams
from core.database.session import get_session
from .schemas import ApiResponse, UniversitySearchParams
from . import services
from typing import Optional



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



@router.get("/academicsinfo", response_model=ApiResponse)
async def list_academics_info(
    query: Optional[str] = Query(None, description="Search academic interests by name"),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await services.get_academics_info(
        query=query,
        page=pagination.page,
        page_size=pagination.pageSize,
        db=db,
    )
    return ApiResponse(message="Academics info fetched successfully", data=data)

