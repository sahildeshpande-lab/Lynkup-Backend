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


@router.get("/majors", response_model=ApiResponse)
async def list_majors(
    query: Optional[str] = Query(None, description="Filter majors by name (case-insensitive)"),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_or_superadmin),
) -> ApiResponse:
    data = await services.list_majors(
        query=query,
        page=page,
        page_size=pageSize,
        db=db,
    )
    message = "No majors found" if data["totalItems"] == 0 else "Majors fetched successfully"
    return ApiResponse(message=message, data=data)


@router.get("/minor", response_model=ApiResponse)
async def list_minors(
    query: Optional[str] = Query(None, description="Filter minors by name (case-insensitive)"),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_or_superadmin),
) -> ApiResponse:
    data = await services.list_minors(
        query=query,
        page=page,
        page_size=pageSize,
        db=db,
    )
    message = "No minors found" if data["totalItems"] == 0 else "Minors fetched successfully"
    return ApiResponse(message=message, data=data)


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
    university_name: list[str] | None = Query(
        default=None,
        description=(
            "Filter by university name or id. Multiple values are OR'd: "
            "repeat the query param and/or use pipe-separated values "
            "(e.g. university_name=id1&university_name=id2 or id1|id2)."
        ),
    ),
    edu_level: list[str] | None = Query(
        default=None,
        description=(
            "Filter by education level name or id from /academicsinfo. "
            "Multiple values are OR'd: repeat the query param and/or use pipe-separated values."
        ),
    ),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_app_user),
) -> ApiResponse:
    data = await services.search_users(
        current_user=current_user,
        db=db,
        query=query,
        university_name=university_name,
        edu_level=edu_level,
        page=page,
        page_size=pageSize,
    )
    return ApiResponse(message="Search results fetched successfully", data=data)


@router.get("/search/posts", response_model=PostSearchResponse)
@router.get("/search/post", response_model=PostSearchResponse, include_in_schema=False)
async def search_posts(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_or_superadmin),
    query: str | None = Query(
        default=None,
        description=(
            "Search post caption/content and author first/last name. "
            "Example: query=Sahil returns posts by authors named Sahil and posts mentioning Sahil."
        ),
    ),
    hashtag: list[str] | None = Query(
        default=None,
        description=(
            "Filter by hashtag tag or hashtag id. Multiple values are OR'd: "
            "repeat the query param and/or use pipe-separated values "
            "(e.g. hashtag=ai&hashtag=ml or ai|ml)."
        ),
    ),
    academic_interest: list[str] | None = Query(
        default=None,
        description=(
            "Filter by author profile academic interest name or id from /academicsinfo. "
            "Multiple values are OR'd: repeat the query param and/or use pipe-separated values "
            "(e.g. academic_interest=AI&academic_interest=NLP or AI|NLP). "
            "Returns posts whose author has any of the selected interests."
        ),
    ),
    university_name: list[str] | None = Query(
        default=None,
        description=(
            "Filter by author university name or university id. Multiple values are OR'd: "
            "repeat the query param and/or use pipe-separated values "
            "(e.g. university_name=id1&university_name=id2 or id1|id2)."
        ),
    ),
    major: str | None = Query(default=None, description="Filter by author major"),
    minor: str | None = Query(default=None, description="Filter by author minor"),
    country: list[str] | None = Query(
        default=None,
        description=(
            "Filter by author profile country name, iso_code, or country id from /academicsinfo. "
            "Multiple values are OR'd: repeat the query param and/or use pipe-separated values "
            "(e.g. country=India&country=US or India|US)."
        ),
    ),
    edu_level: list[str] | None = Query(
        default=None,
        description=(
            "Filter by author education level name or id from /academicsinfo. "
            "Multiple values are OR'd: repeat the query param and/or use pipe-separated values "
            "(e.g. edu_level=1&edu_level=2 or Bachelors|Masters)."
        ),
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


