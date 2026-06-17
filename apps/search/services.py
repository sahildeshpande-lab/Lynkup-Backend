from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.profiles.db_models import Country, University
from common.pagination import build_paginated_response

from .schemas import UniversitySearchParams

from typing import Optional

from apps.profiles.db_models.academic_interests_db_model import AcademicInterest

async def search_universities(params: UniversitySearchParams, db: AsyncSession) -> dict:
    normalized_query = params.query.strip().lower()
    search_terms = normalized_query.split()
    if not search_terms:
        search_terms = [normalized_query]

    from sqlalchemy import and_
    conditions = [func.lower(University.name).like(f"%{term}%") for term in search_terms]
    similarity_score = func.similarity(func.lower(University.name), normalized_query)

    count_stmt = (
        select(func.count())
        .select_from(University)
        .outerjoin(Country, Country.id == University.country_id)
        .where(and_(*conditions))
    )
    total_items = int((await db.execute(count_stmt)).scalar_one())

    stmt = (
        select(University, Country.name.label("country_name"))
        .outerjoin(Country, Country.id == University.country_id)
        .where(and_(*conditions))
        .order_by(similarity_score.desc(), University.name.asc())
        .offset((params.page - 1) * params.pageSize)
        .limit(params.pageSize)
    )
    result = await db.execute(stmt)
    rows = result.all()
    items = [
        {
            "id": str(university.id),
            "name": university.name,
            "country": country_name or "Unknown",
            "slug": university.slug,
            "major": university.major,
            "minor": university.minor,
            "academic_program": university.academic_program,
        }
        for university, country_name in rows
    ]
    paginated = build_paginated_response(items, params.page, params.pageSize, total_items)
    return {"query": params.query, **paginated.model_dump()}



async def get_academic_interests(
    query: Optional[str],
    page: int,
    page_size: int,
    db: AsyncSession,
) -> dict:
    # Build count and select queries
    count_stmt = select(func.count()).select_from(AcademicInterest).where(AcademicInterest.is_active == True)
    stmt = select(AcademicInterest).where(AcademicInterest.is_active == True).order_by(AcademicInterest.name.asc())

    if query:
        clean_query = query.strip()
        if clean_query:
            count_stmt = count_stmt.where(AcademicInterest.name.ilike(f"%{clean_query}%"))
            stmt = stmt.where(AcademicInterest.name.ilike(f"%{clean_query}%"))

    # Execute count query
    total_items = int((await db.execute(count_stmt)).scalar_one())

    # Execute select query with offset and limit
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)
    result = await db.execute(stmt)
    orm_items = result.scalars().all()

    # Format items into serializable list of dicts matching response schema
    items = [
        {
            "id": str(item.id),
            "name": item.name,
        }
        for item in orm_items
    ]

    # Build paginated response using common/pagination helper
    paginated = build_paginated_response(items, page, page_size, total_items)
    return paginated.model_dump()


async def get_academics_info(
    query: Optional[str],
    page: int,
    page_size: int,
    db: AsyncSession,
) -> dict:
    from common.enums import EducationLevel
    edu_levels = [{"id": str(level.id), "name": level.value} for level in EducationLevel]
    interests_data = await get_academic_interests(
        query=query,
        page=page,
        page_size=page_size,
        db=db,
    )
    return {
        "educationLevels": edu_levels,
        "interests": interests_data,
    }
