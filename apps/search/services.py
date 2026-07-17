from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.profiles.db_models import Country, University
from common.pagination import build_paginated_response

from .schemas import UniversitySearchParams

from typing import Optional, TYPE_CHECKING

from apps.profiles.db_models.academic_interests_db_model import AcademicInterest

if TYPE_CHECKING:
    from apps.accounts.db_models import User

async def search_universities(params: UniversitySearchParams, db: AsyncSession) -> dict:
    normalized_query = (params.query or "").strip().lower()

    if normalized_query : 
        search_terms = normalized_query.split()
        if not search_terms:
            search_terms = [normalized_query]

        from sqlalchemy import and_
        conditions = [University.name.ilike(f"%{term}%") for term in search_terms]
        similarity_score = func.similarity(University.name, normalized_query)

        count_stmt = (
            select(func.count())
            .select_from(University)
            .outerjoin(Country, Country.id == University.country_id)
            .where(and_(*conditions))
        )
        stmt=(
            select(University,Country.name.label("country_name"),)
            .outerjoin(Country,Country.id==University.country_id)
            .where(and_(*conditions))
            .order_by(similarity_score.desc(),University.name.asc(),))
    else :
        count_stmt=(
            select(func.count()).select_from(University)
        )
        stmt=(select(University,Country.name.label("country_name"),).outerjoin(Country,Country.id==University.country_id).order_by(University.name.asc()))
    total_items = int((await db.execute(count_stmt)).scalar_one())

    stmt = stmt.offset(
    (params.page - 1) * params.pageSize).limit(params.pageSize)
    result = await db.execute(stmt)
    rows = result.all()
    items = [
        {
            "id": str(university.id),
            "name": university.name,
            "country": country_name or "Unknown",
            "slug": university.slug,
            "website": university.website,
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
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
) -> dict:
    count_stmt = select(func.count()).select_from(AcademicInterest).where(AcademicInterest.is_active == True)
    stmt = select(AcademicInterest).where(AcademicInterest.is_active == True).order_by(AcademicInterest.name.asc())

    if query:
        clean_query = query.strip()
        if clean_query:
            count_stmt = count_stmt.where(AcademicInterest.name.ilike(f"%{clean_query}%"))
            similarity_score = func.similarity(AcademicInterest.name, clean_query)
            stmt = select(AcademicInterest).where(
                AcademicInterest.is_active == True,
                AcademicInterest.name.ilike(f"%{clean_query}%")
            ).order_by(similarity_score.desc(), AcademicInterest.name.asc())

    total_items = int((await db.execute(count_stmt)).scalar_one())
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        resolved_page = page
        resolved_page_size = page_size
    else:
        resolved_page = 1
        resolved_page_size = total_items if total_items > 0 else 1

    orm_items = list((await db.execute(stmt)).scalars().all())
    items = [
        {
            "id": str(item.id),
            "name": item.name,
        }
        for item in orm_items
    ]
    paginated = build_paginated_response(items, resolved_page, resolved_page_size, total_items)
    return paginated.model_dump()


async def _get_education_levels_with_interests(
    db: AsyncSession,
    *,
    query: Optional[str],
) -> list[dict]:
    """Return education levels with nested academic interests."""
    from apps.profiles.db_models.education_level_db_model import EducationLevel

    levels = list(
        (
            await db.execute(
                select(EducationLevel)
                .where(EducationLevel.is_active == True)  # noqa: E712
                .order_by(EducationLevel.id.asc())
            )
        ).scalars().all()
    )

    interests_stmt = (
        select(AcademicInterest)
        .where(AcademicInterest.is_active == True)  # noqa: E712
        .order_by(AcademicInterest.name.asc())
    )
    clean_query = (query or "").strip()
    if clean_query:
        similarity_score = func.similarity(AcademicInterest.name, clean_query)
        interests_stmt = (
            select(AcademicInterest)
            .where(
                AcademicInterest.is_active == True,  # noqa: E712
                AcademicInterest.name.ilike(f"%{clean_query}%"),
            )
            .order_by(similarity_score.desc(), AcademicInterest.name.asc())
        )

    interests = list((await db.execute(interests_stmt)).scalars().all())
    interests_by_level: dict[int, list[dict]] = {}
    for interest in interests:
        interests_by_level.setdefault(interest.education_level_id, []).append(
            {
                "id": str(interest.id),
                "name": interest.name,
            }
        )

    return [
        {
            "id": str(level.id),
            "name": level.name,
            "interests": interests_by_level.get(level.id, []),
        }
        for level in levels
    ]


async def _get_allowed_countries(
    db: AsyncSession,
    *,
    page: int | None,
    page_size: int | None,
) -> dict:
    """Return all countries from the countries table."""
    count_stmt = select(func.count()).select_from(Country)
    stmt = select(Country).order_by(Country.name.asc())
    total_items = int((await db.execute(count_stmt)).scalar_one())

    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        resolved_page = page
        resolved_page_size = page_size
    else:
        resolved_page = 1
        resolved_page_size = total_items if total_items > 0 else 1

    rows = list((await db.execute(stmt)).scalars().all())
    items = [
        {
            "id": str(country.id),
            "name": country.name,
            "iso_code": country.iso_code,
        }
        for country in rows
    ]
    return build_paginated_response(items, resolved_page, resolved_page_size, total_items).model_dump()


async def _get_post_hashtags(
    db: AsyncSession,
    *,
    page: int | None,
    page_size: int | None,
) -> dict:
    """All hashtags from the hashtags table, ordered alphabetically."""
    from apps.feed.db_models import Hashtag

    count_stmt = select(func.count()).select_from(Hashtag)
    total_items = int((await db.execute(count_stmt)).scalar_one())

    stmt = select(Hashtag.id, Hashtag.tag).order_by(Hashtag.tag.asc())
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        resolved_page = page
        resolved_page_size = page_size
    else:
        resolved_page = 1
        resolved_page_size = total_items if total_items > 0 else 1

    rows = list((await db.execute(stmt)).all())
    items = [
        {
            "id": str(row.id),
            "tag": row.tag,
        }
        for row in rows
    ]
    return build_paginated_response(items, resolved_page, resolved_page_size, total_items).model_dump()


async def get_academics_info(
    query: Optional[str],
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
) -> dict:
    education_levels = await _get_education_levels_with_interests(db, query=query)
    countries_data = await _get_allowed_countries(db, page=page, page_size=page_size)
    hashtags_data = await _get_post_hashtags(db, page=page, page_size=page_size)
    return {
        "educationLevels": education_levels,
        "countries": countries_data,
        "hashtags": hashtags_data,
    }


async def search_users(
    current_user: User,
    db: AsyncSession,
    query: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
) -> dict:
    from sqlmodel import select
    from sqlalchemy import func, and_, or_, exists
    from apps.accounts.db_models import User, UserRole, Role
    from apps.profiles.db_models.profile_db_model import Profile
    from apps.profiles.db_models.university_db_model import University
    from common.enums import UserStatus
    from apps.profiles.services import build_user_base_response

    # Base stmt
    stmt = (
        select(User, Profile, University.name.label("university_name"))
        .join(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
    )

    # Exclude admins/superadmins/moderators/viewers using exists subquery
    role_exclude_subquery = exists(
        select(1).where(
            UserRole.user_id == User.id,
            UserRole.role_id == Role.id,
            Role.name.in_(["moderator", "viewer", "superadmin"])
        )
    )
    stmt = stmt.where(~role_exclude_subquery)

    # Account status & not deleted
    stmt = stmt.where(
        User.status == UserStatus.active,
        User.is_deleted == False,
        User.deleted_at.is_(None)
    )

    # Exclude current user
    stmt = stmt.where(User.id != current_user.id)

    # Fuzzy search query (pg_trgm)
    if query and query.strip():
        normalized_query = query.strip()
        search_terms = normalized_query.split()
        conditions = []
        for term in search_terms:
            conditions.append(
                or_(
                    Profile.first_name.ilike(f"%{term}%"),
                    Profile.last_name.ilike(f"%{term}%"),
                    User.email.ilike(f"%{term}%"),
                    University.name.ilike(f"%{term}%"),
                    Profile.major.ilike(f"%{term}%"),
                    Profile.minor.ilike(f"%{term}%"),
                    Profile.edu_level.ilike(f"%{term}%")
                )
            )
        stmt = stmt.where(and_(*conditions))

        full_name_expr = func.concat(Profile.first_name, " ", Profile.last_name)
        similarity_score = func.greatest(
            func.similarity(full_name_expr, normalized_query),
            func.similarity(User.email, normalized_query),
            func.coalesce(func.similarity(University.name, normalized_query), 0.0),
            func.coalesce(func.similarity(Profile.major, normalized_query), 0.0),
            func.coalesce(func.similarity(Profile.minor, normalized_query), 0.0),
            func.coalesce(func.similarity(Profile.edu_level, normalized_query), 0.0)
        )
        stmt = stmt.order_by(similarity_score.desc())
    else:
        stmt = stmt.order_by(Profile.first_name.asc(), Profile.last_name.asc())

    # Build count query for totalItems
    count_stmt = (
        select(func.count(User.id))
        .join(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
    )
    count_stmt = count_stmt.where(~role_exclude_subquery)
    count_stmt = count_stmt.where(
        User.status == UserStatus.active,
        User.is_deleted == False,
        User.deleted_at.is_(None)
    )
    count_stmt = count_stmt.where(User.id != current_user.id)
    if query and query.strip():
        normalized_query = query.strip()
        search_terms = normalized_query.split()
        conditions = []
        for term in search_terms:
            conditions.append(
                or_(
                    Profile.first_name.ilike(f"%{term}%"),
                    Profile.last_name.ilike(f"%{term}%"),
                    User.email.ilike(f"%{term}%"),
                    University.name.ilike(f"%{term}%"),
                    Profile.major.ilike(f"%{term}%"),
                    Profile.minor.ilike(f"%{term}%"),
                    Profile.edu_level.ilike(f"%{term}%")
                )
            )
        count_stmt = count_stmt.where(and_(*conditions))

    # Pagination logic
    if page is not None and page_size is not None:
        total_items = int((await db.execute(count_stmt)).scalar_one())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        rows = result.all()

        target_user_ids = [user.id for user, profile, _university_name in rows]
        from apps.connections.services import get_relationship_flags
        from apps.connections.services.connection_service import apply_relationship_flags
        flags_map = await get_relationship_flags(db, current_user.id, target_user_ids)

        items = []
        for user, profile, university_name in rows:
            user_data = await build_user_base_response(
                user, profile, db, university_name=university_name
            )
            apply_relationship_flags(user_data, flags_map, user.id)
            items.append(user_data)

        from common.pagination import build_paginated_response
        paginated = build_paginated_response(items, page, page_size, total_items)
        return paginated.model_dump()
    else:
        result = await db.execute(stmt)
        rows = result.all()

        target_user_ids = [user.id for user, profile, _university_name in rows]
        from apps.connections.services import get_relationship_flags
        from apps.connections.services.connection_service import apply_relationship_flags
        flags_map = await get_relationship_flags(db, current_user.id, target_user_ids)

        items = []
        for user, profile, university_name in rows:
            user_data = await build_user_base_response(
                user, profile, db, university_name=university_name
            )
            apply_relationship_flags(user_data, flags_map, user.id)
            items.append(user_data)

    return {
        "items": items,
        "page": 1,
        "pageSize": len(items),
        "totalItems": len(items),
        "totalPages": 1
    }


async def search_posts(
    current_user: User,
    db: AsyncSession,
    *,
    query: str | None = None,
    hashtag: str | None = None,
    academic_interest: str | None = None,
    university_name: str | None = None,
    major: str | None = None,
    minor: str | None = None,
    country: str | None = None,
    edu_level: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict:
    from apps.engagement.repositories import fetch_post_engagement_flags
    from apps.engagement.services.reaction_service import format_user_reaction
    from apps.feed.services.post_service import format_post_detail
    from apps.search.repositories import count_search_posts, search_posts_with_details
    from common.pagination import build_paginated_response

    search_kwargs = {
        "query": query,
        "hashtag": hashtag,
        "academic_interest": academic_interest,
        "university_name": university_name,
        "major": major,
        "minor": minor,
        "country": country,
        "edu_level": edu_level,
    }

    async def _format_items(rows):
        from apps.engagement.services.post_reaction_formatters import load_latest_post_reactions

        post_ids = [post.id for post, *_ in rows]
        engagement_flags = await fetch_post_engagement_flags(db, current_user.id, post_ids)
        latest_reactions = await load_latest_post_reactions(db, post_ids, per_type_limit=3)
        items = [
            format_post_detail(
                post,
                author_profile=author_profile,
                moderator_user=mod_user,
                moderator_profile=mod_profile,
                is_liked=engagement_flags.user_reaction_for(post.id) is not None,
                is_reposted=post.id in engagement_flags.reposted_post_ids,
                is_bookmarked=post.id in engagement_flags.bookmarked_post_ids,
                user_reaction=format_user_reaction(engagement_flags.user_reaction_for(post.id)),
                reactions=latest_reactions.get(post.id),
            )
            for post, author_profile, mod_user, mod_profile in rows
        ]
        for item in items:
            item["reaction_count"] = item["like_count"]
        return items

    if page is not None and page_size is not None:
        total = await count_search_posts(db, current_user.id, **search_kwargs)
        rows = await search_posts_with_details(
            db,
            current_user.id,
            **search_kwargs,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        items = await _format_items(rows)
        return build_paginated_response(items, page, page_size, total).model_dump()

    rows = await search_posts_with_details(
        db,
        current_user.id,
        **search_kwargs,
        offset=0,
        limit=None,
    )
    items = await _format_items(rows)
    return {
        "items": items,
        "page": 1,
        "pageSize": len(items),
        "totalItems": len(items),
        "totalPages": 1 if items else 0,
    }
