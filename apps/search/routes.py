from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status 
from sqlalchemy.ext.asyncio import AsyncSession

from common.pagination import PaginationParams
from core.database.session import get_session
from core.security.auth import get_current_user
from apps.accounts.db_models import User
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
    normalized_query = query.strip() if query else ""
    if normalized_query and len(normalized_query) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query must be at least 3 characters",
        )

    data = await services.search_universities(
        UniversitySearchParams(query=normalized_query, page=pagination.page, pageSize=pagination.pageSize),
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


@router.get("/search-user", response_model=ApiResponse)
async def searchuser(
    query: Optional[str] = Query(None, description="Fuzzy search name, email, university, major, minor, or education level"),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ApiResponse:
    data = await services.search_users(
        current_user=current_user,
        db=db,
        query=query,
        page=page,
        page_size=pageSize,
    )
    return ApiResponse(message="Search results fetched successfully", data=data)



