from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import CatalogOrder, CatalogSort
from common.pagination import OptionalPaginationParams
from core.database.session import get_session
from core.security.auth import (
    get_current_app_user,
    get_current_user_or_superadmin,
    get_current_user_moderator_or_superadmin,
)
from apps.accounts.db_models import User
from apps.academics import services as academic_catalog_services
from apps.academics.schemas import UserAcademicInterestCreate
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
    sort: CatalogSort | None = Query(
        default=None,
        description="Sort column. created_at (default).",
    ),
    order: CatalogOrder = Query(
        default=CatalogOrder.desc,
        description="Sort direction. desc = newest/latest first (default); asc = oldest first.",
    ),
    pagination: OptionalPaginationParams = Depends(),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:
    normalized_query = query.strip() if query else ""

    data = await services.search_universities(
        UniversitySearchParams(
            query=normalized_query,
            page=pagination.page,
            pageSize=pagination.pageSize,
            sort=sort,
            order=order,
            sort_by=sort,
            order_by=order,
        ),
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
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:
    data = await services.get_academics_info(
        query=query,
        page=page,
        page_size=pageSize,
        db=db,
    )
    return ApiResponse(message="Academics info fetched successfully", data=data)


@router.get("/countries", response_model=ApiResponse)
async def list_countries(
    query: Optional[str] = Query(None, description="Filter countries by name or iso_code (case-insensitive)"),
    sort: CatalogSort | None = Query(
        default=None,
        description=(
            "Sort column. Omit for alphabetical name (A-Z / Z-A via order). "
            "Pass created_at to sort by last activity (greatest of created_at, updated_at)."
        ),
    ),
    order: CatalogOrder = Query(
        default=CatalogOrder.asc,
        description=(
            "Sort direction. Default asc: name A-Z, or oldest last-activity first when "
            "sort=created_at. desc: name Z-A, or newest last-activity first."
        ),
    ),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:

    data = await services.list_countries(
        query=query,
        page=page,
        page_size=pageSize,
        sort=sort,
        order=order,
        db=db,
    )
    message = "No countries found" if data["totalItems"] == 0 else "Countries fetched successfully"
    return ApiResponse(message=message, data=data)


@router.get("/majors", response_model=ApiResponse)
async def list_majors(
    query: Optional[str] = Query(None, description="Filter majors by name (case-insensitive)"),
    page: Optional[int] = Query(None, ge=1, description="Page number for pagination"),
    pageSize: Optional[int] = Query(None, ge=1, le=200, description="Page size for pagination"),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
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
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:
    data = await services.list_minors(
        query=query,
        page=page,
        page_size=pageSize,
        db=db,
    )
    message = "No minors found" if data["totalItems"] == 0 else "Minors fetched successfully"
    return ApiResponse(message=message, data=data)


@router.get("/major", response_model=ApiResponse)
async def list_test_majors(
    query: str | None = Query(None, description="Filter majors by name (case-insensitive)"),
    sort: CatalogSort | None = Query(
        default=None,
        description="Sort column. created_at (default).",
    ),
    order: CatalogOrder = Query(
        default=CatalogOrder.desc,
        description="Sort direction. desc = newest/latest first (default); asc = oldest first.",
    ),
    pagination: OptionalPaginationParams = Depends(),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:
    _ = current_user
    data = await academic_catalog_services.list_test_majors(
        query=query,
        page=pagination.page,
        page_size=pagination.pageSize,
        sort=sort,
        order=order,
        db=db,
    )
    message = "No majors found" if data["totalItems"] == 0 else "Majors fetched successfully"
    return ApiResponse(message=message, data=data)


@router.get("/minors", response_model=ApiResponse)
async def list_test_minors(
    query: str | None = Query(None, description="Filter minors by name (case-insensitive)"),
    sort: CatalogSort | None = Query(
        default=None,
        description="Sort column. created_at (default).",
    ),
    order: CatalogOrder = Query(
        default=CatalogOrder.desc,
        description="Sort direction. desc = newest/latest first (default); asc = oldest first.",
    ),
    pagination: OptionalPaginationParams = Depends(),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:
    _ = current_user
    data = await academic_catalog_services.list_test_minors(
        query=query,
        page=pagination.page,
        page_size=pagination.pageSize,
        sort=sort,
        order=order,
        db=db,
    )
    message = "No minors found" if data["totalItems"] == 0 else "Minors fetched successfully"
    return ApiResponse(message=message, data=data)


@router.get("/interest", response_model=ApiResponse)
async def list_test_interests(
    major_id: int | None = Query(
        None,
        description="Optional major catalog id. Alone: all interests for that major. "
        "With minor_id: interests matching major_id OR minor_id.",
    ),
    minor_id: int | None = Query(
        None,
        description="Optional minor catalog id. Alone: all interests for that minor. "
        "With major_id: interests matching major_id OR minor_id.",
    ),
    query: str | None = Query(None, description="Filter interests by name (case-insensitive)"),
    sort: CatalogSort | None = Query(
        default=None,
        description="Sort column. created_at (default).",
    ),
    order: CatalogOrder = Query(
        default=CatalogOrder.desc,
        description="Sort direction. desc = newest/latest first (default); asc = oldest first.",
    ),
    pagination: OptionalPaginationParams = Depends(),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:

    _ = current_user
    data = await academic_catalog_services.list_test_interests(
        major_id=major_id,
        minor_id=minor_id,
        query=query,
        page=pagination.page,
        page_size=pagination.pageSize,
        sort=sort,
        order=order,
        db=db,
    )
    message = "No interests found" if data["totalItems"] == 0 else "Interests fetched successfully"
    return ApiResponse(message=message, data=data)




@router.post(
    "/academic-interests",
    response_model=ApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_academic_interest(
    payload: AcademicInterestCreate,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:
    data = await services.create_academic_interest(
        name=payload.name,
        education_level_id=payload.educationLevelId,
        db=db,
    )
    return ApiResponse(message="Academic interest created successfully", data=data)


@router.post(
    "/academic-interest",
    response_model=ApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user_academic_interests(
    payload: UserAcademicInterestCreate,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> ApiResponse:
    _ = current_user
    data = await academic_catalog_services.create_user_academic_interests(
        major=payload.major,
        minor=payload.minor,
        interests=payload.interests,
        db=db,
    )
    return ApiResponse(message="Academic interests processed successfully", data=data)


@router.get("/search-user", response_model=ApiResponse)
async def searchuser(
    query: Optional[str] = Query(
        None,
        description=(
            "Search name, email, or university (partial). "
            "Major matches the full query exactly (e.g. CS returns only CS majors, "
            "not Computer Science or Cyber Security). Minor is not searched."
        ),
    ),
    university_name: list[str] | None = Query(
        default=None,
        description=(
            "Filter by university name or id. Multiple values are OR'd: "
            "repeat the query param and/or use pipe-separated values "
            "(e.g. university_name=id1&university_name=id2 or id1|id2)."
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
        page=page,
        page_size=pageSize,
    )
    return ApiResponse(message="Search results fetched successfully", data=data)


@router.get("/search/posts", response_model=PostSearchResponse)

async def search_posts(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
    query: str | None = Query(
        default=None,
        description=(
            "Search post caption/content, author first/last name, or author major/minor. "
            "Keyword match is a case-insensitive prefix (e.g. managem matches management). "
            "Example: query=Computer Science returns posts from authors with that major "
            "and posts whose caption/content mentions the term."
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
    hashtag_to_all: bool = Query(
        default=False,
        description=(
            "When false or omitted, hashtag uses existing filtering "
            "(restrict to the selected values; multiple values are OR'd). "
            "When true, do not restrict by hashtag; include all values for this filter."
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
    academic_interest_to_all: bool = Query(
        default=False,
        description=(
            "When false or omitted, academic_interest uses existing filtering "
            "(restrict to the selected values; multiple values are OR'd). "
            "When true, do not restrict by academic interest; include all values for this filter."
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
    university_to_all: bool = Query(
        default=False,
        description=(
            "When false or omitted, university_name uses existing filtering "
            "(restrict to the selected values; multiple values are OR'd). "
            "When true, do not restrict by university; include all values for this filter."
        ),
    ),
    major: list[str] | None = Query(
        default=None,
        description=(
            "Filter by author major. Multiple values are OR'd: "
            "repeat the query param and/or use pipe-separated values "
            "(e.g. major=Computer Science&major=Information Technology or Computer Science|IT)."
        ),
    ),
    major_to_all: bool = Query(
        default=False,
        description=(
            "When false or omitted, major uses existing filtering "
            "(restrict to the selected values; multiple values are OR'd). "
            "When true, do not restrict by major; include all values for this filter."
        ),
    ),
    minor: list[str] | None = Query(
        default=None,
        description=(
            "Filter by author minor. Multiple values are OR'd: "
            "repeat the query param and/or use pipe-separated values "
            "(e.g. minor=AI&minor=Data Science or AI|Data Science)."
        ),
    ),
    minor_to_all: bool = Query(
        default=False,
        description=(
            "When false or omitted, minor uses existing filtering "
            "(restrict to the selected values; multiple values are OR'd). "
            "When true, do not restrict by minor; include all values for this filter."
        ),
    ),
    country: list[str] | None = Query(
        default=None,
        description=(
            "Filter by author profile country name, iso_code, or country id from /countries. "
            "Multiple values are OR'd: repeat the query param and/or use pipe-separated values "
            "(e.g. country=India&country=US or India|US)."
        ),
    ),
    country_to_all: bool = Query(
        default=False,
        description=(
            "When false or omitted, country uses existing filtering "
            "(restrict to the selected values; multiple values are OR'd). "
            "When true, do not restrict by country; include all values for this filter."
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
    edu_level_to_all: bool = Query(
        default=False,
        description=(
            "When false or omitted, edu_level uses existing filtering "
            "(restrict to the selected values; multiple values are OR'd). "
            "When true, do not restrict by education level; include all values for this filter."
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
        hashtag_to_all=hashtag_to_all,
        academic_interest=academic_interest,
        academic_interest_to_all=academic_interest_to_all,
        university_name=university_name,
        university_to_all=university_to_all,
        major=major,
        major_to_all=major_to_all,
        minor=minor,
        minor_to_all=minor_to_all,
        country=country,
        country_to_all=country_to_all,
        edu_level=edu_level,
        edu_level_to_all=edu_level_to_all,
        page=page,
        page_size=pageSize,
    )
    message = "Posts fetched successfully" if data["totalItems"] else "No posts found"
    return PostSearchResponse(status=True, message=message, data=data)


