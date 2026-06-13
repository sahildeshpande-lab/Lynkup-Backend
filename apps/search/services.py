from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.profiles.db_models import Country, University
from common.pagination import build_paginated_response

from .schemas import UniversitySearchParams


async def search_universities(params: UniversitySearchParams, db: AsyncSession) -> dict:
    normalized_query = params.query.strip().lower()
    similarity_score = func.similarity(func.lower(University.name), normalized_query)

    count_stmt = (
        select(func.count())
        .select_from(University)
        .join(Country, Country.id == University.country_id)
        .where(func.lower(University.name).like(f"%{normalized_query}%"))
    )
    total_items = int((await db.execute(count_stmt)).scalar_one())

    stmt = (
        select(University, Country.name.label("country_name"))
        .join(Country, Country.id == University.country_id)
        .where(func.lower(University.name).like(f"%{normalized_query}%"))
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
            "country": country_name,
            "slug": university.slug,
            "minor": university.minor,
        }
        for university, country_name in rows
    ]
    paginated = build_paginated_response(items, params.page, params.pageSize, total_items)
    return {"query": params.query, **paginated.model_dump()}
