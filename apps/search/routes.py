from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from common.exceptions import ApiError
from sqlalchemy.ext.asyncio import AsyncSession

from common.pagination import PaginationParams
from core.database.session import get_session
from core.security.auth import get_current_app_user, get_current_user_or_superadmin
from apps.accounts.db_models import User
from .schemas import (
    AcademicInterestCreate,
    ApiResponse,
    PostSearchResponse,
    UniversitySearchParams,
)
from . import services
from typing import Optional




router = APIRouter(tags=["3] Search & Discovery"])


@router.get("/universities", response_model=ApiResponse)
async def universities(
    query: str | None = Query(default=None),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_or_superadmin),
) -> ApiResponse:
    normalized_query = query.strip() if query else ""
    if normalized_query and len(normalized_query) < 3:
        raise ApiError("Search query must be at least 3 characters")

    data = await services.search_universities(
        UniversitySearchParams(query=normalized_query, page=pagination.page, pageSize=pagination.pageSize),
        db,
    )
    message=("No universities found" if data["totalItems"]==0 else "Universities fetched successfully")
    return ApiResponse(message=message, data=data)



@router.get("/academicsinfo", response_model=ApiResponse)
async def list_academics_info(
    query: Optional[str] = Query(None, description="Search academic interests by name"),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_or_superadmin),
) -> ApiResponse:
    data = await services.get_academics_info(
        query=query,
        page=page,
        page_size=pageSize,
        db=db,
    )
    return ApiResponse(message="Academics info fetched successfully", data=data)


@router.post(
    "/academic-interests",
    response_model=ApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_academic_interest(
    payload: AcademicInterestCreate,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_or_superadmin),
) -> ApiResponse:
    data = await services.create_academic_interest(
        name=payload.name,
        education_level_id=payload.educationLevelId,
        db=db,
    )
    return ApiResponse(message="Academic interest created successfully", data=data)


@router.get("/search-user", response_model=ApiResponse)
async def searchuser(
    query: Optional[str] = Query(None, description="Fuzzy search name, email, university, major, minor, or education level"),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_app_user),
) -> ApiResponse:
    data = await services.search_users(
        current_user=current_user,
        db=db,
        query=query,
        page=page,
        page_size=pageSize,
    )
    return ApiResponse(message="Search results fetched successfully", data=data)


@router.get("/search/posts", response_model=PostSearchResponse)
@router.get("/search/post", response_model=PostSearchResponse, include_in_schema=False)
async def search_posts(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_or_superadmin),
    query: str | None = Query(default=None, description="Search post caption and content"),
    hashtag: str | None = Query(
        default=None,
        description="Filter by hashtag tag or hashtag id from /academicsinfo",
    ),
    academic_interest: str | None = Query(
        default=None,
        description="Filter by author academic interest name or id from /academicsinfo",
    ),
    university_name: str | None = Query(
        default=None,
        description="Filter by author university name or university id",
    ),
    major: str | None = Query(default=None, description="Filter by author major"),
    minor: str | None = Query(default=None, description="Filter by author minor"),
    country: str | None = Query(
        default=None,
        description="Filter by author country name, iso_code, or country id from /academicsinfo",
    ),
    edu_level: str | None = Query(
        default=None,
        description="Filter by author education level name or id from /academicsinfo",
    ),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
) -> PostSearchResponse:
    data = await services.search_posts(
        current_user=current_user,
        db=db,
        query=query,
        hashtag=hashtag,
        academic_interest=academic_interest,
        university_name=university_name,
        major=major,
        minor=minor,
        country=country,
        edu_level=edu_level,
        page=page,
        page_size=pageSize,
    )
    message = "Posts fetched successfully" if data["totalItems"] else "No posts found"
    return PostSearchResponse(status=True, message=message, data=data)


