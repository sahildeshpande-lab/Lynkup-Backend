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
    query: str | None = Query(default=None),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await services.search_universities(
        UniversitySearchParams(query=query.strip() if query else "" , page=pagination.page, pageSize=pagination.pageSize),
        db,
    )
    message=("No universities found" if data["totalItems"]==0 else "Universities fetched successfully")
    return ApiResponse(message=message, data=data)



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

