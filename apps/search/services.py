from __future__ import annotations

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.profiles.db_models import Country, University
from apps.profiles.normalization import (
    collect_normalized_program_names,
    normalize_named_program_list,
)
from common.pagination import build_paginated_response, paginate_items

from .schemas import UniversitySearchParams

from typing import Optional, TYPE_CHECKING

from apps.profiles.db_models.academic_interests_db_model import AcademicInterest

if TYPE_CHECKING:
    from apps.accounts.db_models import User

async def search_universities(params: UniversitySearchParams, db: AsyncSession) -> dict:
    normalized_query = (params.query or "").strip().lower()

    sort_raw = getattr(params, "sort", None) or getattr(params, "sort_by", None)
    order_raw = getattr(params, "order", None) or getattr(params, "order_by", None)
    sort_val = sort_raw.value if hasattr(sort_raw, "value") else sort_raw
    order_val = order_raw.value if hasattr(order_raw, "value") else order_raw

    sort_field = (sort_val or "").strip().lower()
    if sort_field == "created_at":
        order_dir = (order_val or "desc").strip().lower()
        target_col = getattr(University, "created_at", getattr(University, "updated_at", University.name))
        primary_order = target_col.asc() if order_dir == "asc" else target_col.desc()
        custom_orders = [primary_order, University.name.asc()]
    elif order_val:
        order_dir = order_val.strip().lower()
        primary_order = University.name.desc() if order_dir == "desc" else University.name.asc()
        custom_orders = [primary_order]
    else:
        custom_orders = None




    base_conditions = [University.is_active == True]  # noqa: E712

    if normalized_query:
        search_terms = normalized_query.split()
        if not search_terms:
            search_terms = [normalized_query]

        conditions = base_conditions + [University.name.ilike(f"%{term}%") for term in search_terms]
        similarity_score = func.similarity(University.name, normalized_query)

        count_stmt = (
            select(func.count())
            .select_from(University)
            .outerjoin(Country, Country.id == University.country_id)
            .where(and_(*conditions))
        )
        order_clauses = custom_orders if custom_orders is not None else [similarity_score.desc(), University.name.asc()]
        stmt = (
            select(
                University,
                Country.name.label("country_name"),
            )
            .outerjoin(Country, Country.id == University.country_id)
            .where(and_(*conditions))
            .order_by(*order_clauses)
        )
    else:
        count_stmt = (
            select(func.count()).select_from(University).where(and_(*base_conditions))
        )
        order_clauses = custom_orders if custom_orders is not None else [University.name.asc()]
        stmt = (
            select(
                University,
                Country.name.label("country_name"),
            )
            .outerjoin(Country, Country.id == University.country_id)
            .where(and_(*base_conditions))
            .order_by(*order_clauses)
        )

    total_items = int((await db.execute(count_stmt)).scalar_one())

    if params.page is not None and params.pageSize is not None:
        stmt = stmt.offset((params.page - 1) * params.pageSize).limit(params.pageSize)
        resolved_page = params.page
        resolved_page_size = params.pageSize
    else:
        resolved_page = 1
        resolved_page_size = total_items if total_items > 0 else 1

    result = await db.execute(stmt)
    rows = result.all()
    items = [
        {
            "id": str(university.id),
            "name": university.name,
            "country": country_name or "Unknown",
            "slug": university.slug,
            "website": university.website,
            "major": normalize_named_program_list(university.major),
            "minor": normalize_named_program_list(university.minor),
            "academic_program": university.academic_program,
        }
        for university, country_name in rows
    ]
    paginated = build_paginated_response(items, resolved_page, resolved_page_size, total_items)
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


async def create_academic_interest(
    name: str,
    education_level_id: int,
    db: AsyncSession,
) -> dict:
    from apps.profiles.db_models.education_level_db_model import EducationLevel
    from apps.profiles.services.interest_service import _find_existing_academic_interest
    from common.exceptions import ApiError
    from sqlalchemy.exc import IntegrityError

    education_level = (
        await db.execute(
            select(EducationLevel).where(
                EducationLevel.id == education_level_id,
                EducationLevel.is_active == True,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if education_level is None:
        raise ApiError("Education level not found")

    # Reject exact matches (any casing) and near-duplicates within the same education level.
    existing = await _find_existing_academic_interest(
        name,
        db,
        education_level_id=education_level_id,
    )
    if existing is not None:
        raise ApiError("Academic interest already exists")

    interest = AcademicInterest(
        name=name,
        education_level_id=education_level_id,
        is_active=True,
    )
    db.add(interest)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError("Academic interest already exists") from None
    await db.refresh(interest)

    return {
        "id": str(interest.id),
        "name": interest.name,
        "educationLevelId": str(interest.education_level_id),
        "isActive": interest.is_active,
    }


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


async def list_countries(
    query: Optional[str],
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    sort: str | None = None,
    order: str | None = None,
) -> dict:
    """Return countries from the countries table, optionally filtered by name/iso_code."""
    filters = [Country.is_active == True]  # noqa: E712
    if query and query.strip():
        needle = f"%{query.strip()}%"
        filters.append(
            or_(
                Country.name.ilike(needle),
                Country.iso_code.ilike(needle),
            )
        )

    count_stmt = select(func.count()).select_from(Country)

    sort_val = sort.value if hasattr(sort, "value") else sort
    order_val = order.value if hasattr(order, "value") else order

    sort_field = (sort_val or "").strip().lower()
    order_dir = (order_val or "asc").strip().lower()
    if sort_field == "created_at":
        # Last activity = max(created_at, updated_at). CASE is portable across PG/SQLite.
        last_activity = case(
            (Country.updated_at > Country.created_at, Country.updated_at),
            else_=Country.created_at,
        )
        order_clause = [
            last_activity.asc() if order_dir == "asc" else last_activity.desc(),
            Country.name.asc(),
            Country.id.asc(),
        ]
    else:
        # Default: alphabetical by name (A-Z / Z-A)
        order_clause = [
            Country.name.desc() if order_dir == "desc" else Country.name.asc(),
            Country.id.asc(),
        ]

    stmt = select(Country).order_by(*order_clause)

    if filters:
        count_stmt = count_stmt.where(*filters)
        stmt = stmt.where(*filters)


    total_items = int((await db.execute(count_stmt)).scalar_one())

    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        resolved_page = page
        resolved_page_size = page_size
    else:
        resolved_page = 1
        resolved_page_size = total_items if total_items > 0 else 1

    rows = list((await db.execute(stmt)).scalars().all())
    items = []
    for country in rows:
        created_at_value = getattr(country, "created_at", None)
        items.append(
            {
                "id": str(country.id),
                "name": country.name,
                "iso_code": country.iso_code,
                "created_at": created_at_value.isoformat() if created_at_value else None,
            }
        )
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
    countries_data = await list_countries(query=None, page=None, page_size=None, db=db)
    hashtags_data = await _get_post_hashtags(db, page=page, page_size=page_size)
    return {
        "educationLevels": education_levels,
        "countries": countries_data,
        "hashtags": hashtags_data,
    }


async def _list_program_field(
    *,
    field: str,
    query: Optional[str],
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
) -> dict:
    """Return title-cased, deduped major or minor names from universities + profiles."""
    from apps.profiles.db_models.profile_db_model import Profile

    if field == "major":
        university_column = University.major
        profile_column = Profile.major
    elif field == "minor":
        university_column = University.minor
        profile_column = Profile.minor
    else:
        raise ValueError(f"Unsupported program field: {field}")

    university_rows = list(
        (
            await db.execute(
                select(university_column).where(University.is_active == True)  # noqa: E712
            )
        )
        .scalars()
        .all()
    )
    profile_rows = list(
        (
            await db.execute(
                select(profile_column).where(
                    profile_column.is_not(None),
                    profile_column != "",
                )
            )
        )
        .scalars()
        .all()
    )

    names = collect_normalized_program_names(*university_rows, *profile_rows)

    if query and query.strip():
        needle = query.strip().lower()
        names = [name for name in names if needle in name.lower()]

    items = [{"name": name} for name in names]
    if page is not None and page_size is not None:
        return paginate_items(items, page=page, page_size=page_size).model_dump()

    total_items = len(items)
    return build_paginated_response(
        items,
        page=1,
        page_size=total_items if total_items > 0 else 1,
        total_items=total_items,
    ).model_dump()


async def list_majors(
    query: Optional[str],
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
) -> dict:
    return await _list_program_field(
        field="major",
        query=query,
        page=page,
        page_size=page_size,
        db=db,
    )


def _exact_profile_major_clause(major_query: str):
    """Match ``Profile.major`` exactly (case-insensitive, trimmed)."""
    from apps.profiles.db_models.profile_db_model import Profile

    cleaned = major_query.strip()
    if not cleaned:
        return None
    return func.lower(func.trim(Profile.major)) == cleaned.lower()


def _search_users_text_clause(query: str):
    """Name/email/university stay fuzzy; major is an exact full-form match.

    ``CS`` matches major ``CS`` only, not Computer Science or Cyber Security.
    Minor is not searched.
    """
    from apps.accounts.db_models import User
    from apps.profiles.db_models.profile_db_model import Profile
    from apps.profiles.db_models.university_db_model import University

    normalized_query = query.strip()
    if not normalized_query:
        return None

    search_terms = [term for term in normalized_query.split() if term]
    term_clauses = []
    for term in search_terms:
        field_matches = [
            Profile.first_name.ilike(f"%{term}%"),
            Profile.last_name.ilike(f"%{term}%"),
            User.email.ilike(f"%{term}%"),
            University.name.ilike(f"%{term}%"),
        ]
        major_clause = _exact_profile_major_clause(term)
        if major_clause is not None:
            field_matches.append(major_clause)
        term_clauses.append(or_(*field_matches))

    combined = and_(*term_clauses)
    full_major = _exact_profile_major_clause(normalized_query)
    if full_major is not None:
        return or_(combined, full_major)
    return combined


async def list_minors(
    query: Optional[str],
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
) -> dict:
    return await _list_program_field(
        field="minor",
        query=query,
        page=page,
        page_size=page_size,
        db=db,
    )


async def search_users(
    current_user: User,
    db: AsyncSession,
    query: Optional[str] = None,
    university_name: str | list[str] | None = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
) -> dict:
    from sqlmodel import select
    from sqlalchemy import func, and_, exists
    from apps.accounts.db_models import User, UserRole, Role
    from apps.profiles.db_models.profile_db_model import Profile
    from apps.profiles.db_models.university_db_model import University
    from apps.search.repositories.post_search_repository import (
        _split_filter_values,
        _university_match_clause,
    )
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

    structured_filters = []
    university_clause = _university_match_clause(
        _split_filter_values(university_name),
        university_id_column=Profile.university_id,
    )
    if university_clause is not None:
        structured_filters.append(university_clause)
    if structured_filters:
        stmt = stmt.where(and_(*structured_filters))

    # Name/email/university are fuzzy; major is exact. Minor is not searched.
    text_clause = None
    if query and query.strip():
        normalized_query = query.strip()
        text_clause = _search_users_text_clause(normalized_query)
        stmt = stmt.where(text_clause)

        full_name_expr = func.concat(Profile.first_name, " ", Profile.last_name)
        similarity_score = func.greatest(
            func.similarity(full_name_expr, normalized_query),
            func.similarity(User.email, normalized_query),
            func.coalesce(func.similarity(University.name, normalized_query), 0.0),
            func.coalesce(func.similarity(Profile.major, normalized_query), 0.0),
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
    if structured_filters:
        count_stmt = count_stmt.where(and_(*structured_filters))
    if text_clause is not None:
        count_stmt = count_stmt.where(text_clause)

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
    hashtag: str | list[str] | None = None,
    hashtag_to_all: bool = False,
    academic_interest: str | list[str] | None = None,
    academic_interest_to_all: bool = False,
    university_name: str | list[str] | None = None,
    university_to_all: bool = False,
    major: str | list[str] | None = None,
    major_to_all: bool = False,
    minor: str | list[str] | None = None,
    minor_to_all: bool = False,
    country: str | list[str] | None = None,
    country_to_all: bool = False,
    edu_level: str | list[str] | None = None,
    edu_level_to_all: bool = False,
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
        "hashtag_to_all": hashtag_to_all,
        "academic_interest": academic_interest,
        "academic_interest_to_all": academic_interest_to_all,
        "university_name": university_name,
        "university_to_all": university_to_all,
        "major": major,
        "major_to_all": major_to_all,
        "minor": minor,
        "minor_to_all": minor_to_all,
        "country": country,
        "country_to_all": country_to_all,
        "edu_level": edu_level,
        "edu_level_to_all": edu_level_to_all,
    }

    async def _format_items(rows):
        from apps.connections.services.recommendation_service import get_user_connections
        from apps.engagement.services.post_reaction_formatters import load_latest_post_reactions
        from apps.feed.services.profile_enrichment import (
            load_profile_details,
            load_requested_user_ids,
        )

        post_ids = [post.id for post, *_ in rows]
        profiles_by_user_id = {
            post.author_user_id: author_profile
            for post, author_profile, *_ in rows
            if author_profile is not None
        }
        connection_ids = await get_user_connections(db, current_user.id)
        requested_user_ids = await load_requested_user_ids(
            db,
            current_user.id,
            set(profiles_by_user_id),
        )
        profile_details = await load_profile_details(db, profiles_by_user_id)
        engagement_flags = await fetch_post_engagement_flags(db, current_user.id, post_ids)
        latest_reactions = await load_latest_post_reactions(db, post_ids, per_type_limit=3)
        items = [
            format_post_detail(
                post,
                author_profile=author_profile,
                moderator_user=mod_user,
                moderator_profile=mod_profile,
                is_connected=post.author_user_id in connection_ids,
                is_requested=post.author_user_id in requested_user_ids,
                profile_details=profile_details.get(post.author_user_id),
                is_liked=engagement_flags.user_reaction_for(post.id) is not None,
                is_reposted=post.id in engagement_flags.reposted_post_ids,
                is_bookmarked=post.id in engagement_flags.bookmarked_post_ids,
                user_reaction=format_user_reaction(engagement_flags.user_reaction_for(post.id)),
                reactions=latest_reactions.get(post.id),
                viewer_user_id=current_user.id,
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
