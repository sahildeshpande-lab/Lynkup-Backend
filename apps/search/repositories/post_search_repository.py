from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import and_, cast, exists, false, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from apps.accounts.db_models import User
from apps.connections.db_models import Block
from apps.connections.services.recommendation_service import get_user_connections
from apps.feed.db_models import Hashtag, Post, PostAttachment, PostHashtag
from apps.profiles.db_models import Country, Profile
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.university_db_model import University
from common.enums import EducationLevel, PostState, ProfileVisibility
from common.user_visibility import visible_user_filters
from sqlalchemy.dialects.postgresql import JSONB


def _try_parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value.strip())
    except (TypeError, ValueError, AttributeError):
        return None


def _is_compact_filter_token(value: str) -> bool:
    """True for ids/tags safe to split on commas (not multi-word names)."""
    cleaned = value.strip()
    if not cleaned:
        return False
    if _try_parse_uuid(cleaned) is not None or cleaned.isdigit():
        return True
    # Hashtag-like tokens: no spaces, short enough to be a tag/slug.
    return " " not in cleaned and len(cleaned) <= 64


def _split_filter_values(
    value: str | list[str] | None,
    *,
    split_whitespace: bool = False,
) -> list[str]:
    """Normalize filter values from repeated query params or joined strings.

    Commas only split when every token looks like an id/tag so names such as
    "University of California, Berkeley" stay intact. Pipe/semicolon always split.
    """
    if value is None:
        return []
    raw_parts = value if isinstance(value, list) else [value]
    values: list[str] = []
    seen: set[str] = set()

    def _add(cleaned: str) -> None:
        key = cleaned.lower()
        if key in seen:
            return
        seen.add(key)
        values.append(cleaned)

    for part in raw_parts:
        if part is None:
            continue
        text = str(part).strip()
        if not text:
            continue

        if re.search(r"[|;]", text):
            tokens = re.split(r"[|;]", text)
        elif "," in text:
            comma_tokens = [token.strip() for token in text.split(",") if token.strip()]
            if comma_tokens and all(_is_compact_filter_token(token) for token in comma_tokens):
                tokens = comma_tokens
            else:
                tokens = [text]
        elif split_whitespace:
            tokens = re.split(r"\s+", text)
        else:
            tokens = [text]

        for token in tokens:
            cleaned = token.strip()
            if cleaned:
                _add(cleaned)
    return values


def _normalize_hashtag(value: str) -> str:
    return value.strip().lstrip("#").lower()


def _resolve_edu_level(value: str) -> str | None:
    """Map academicsinfo id/name values to the stored profile.edu_level string."""
    raw = value.strip()
    if not raw:
        return None
    if raw.isdigit():
        try:
            return EducationLevel.from_id(int(raw)).value
        except ValueError:
            return None
    aliases = {
        "undergraduate": "Bachelors",
        "undergrad": "Bachelors",
        "bachelor": "Bachelors",
        "bachelors": "Bachelors",
        "graduate": "Masters",
        "masters": "Masters",
        "master": "Masters",
        "phd": "Doctorate",
        "ph.d": "Doctorate",
        "ph.d.": "Doctorate",
        "doctorate": "Doctorate",
        "doctoral": "Doctorate",
        "postdoctoral": "Postdoctoral",
        "postdoc": "Postdoctoral",
        "jd": "JD",
        "md": "MD",
    }
    alias = aliases.get(raw.lower())
    if alias:
        return alias
    for level in EducationLevel:
        if level.value.lower() == raw.lower() or level.name.lower() == raw.lower():
            return level.value
    return None


def _escape_like_exact(value: str) -> str:
    """Escape LIKE wildcards so interest names match exactly (case-insensitive)."""
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _word_boundary_match(column, term: str):
    """Case-insensitive whole-word/phrase match (Postgres ``\\y`` boundaries).

    Prevents substring hits such as query ``ai`` matching ``Argentina``.
    """
    pattern = rf"\y{re.escape(term)}\y"
    return column.op("~*")(pattern)


def _profile_interest_contains(author_profile, interest_id):
    """Match interest ids stored as JSON numbers or JSON strings.

    ``interest_id`` may be a Python int or an ``AcademicInterest.id`` column.
    """
    from sqlalchemy import String

    interests_json = func.coalesce(
        cast(author_profile.profile_interests_id, JSONB),
        cast(text("'[]'"), JSONB),
    )
    return or_(
        interests_json.contains(func.jsonb_build_array(interest_id)),
        interests_json.contains(func.jsonb_build_array(cast(interest_id, String))),
    )


def _hashtag_match_clause(values: list[str]):
    clauses = []
    for value in values:
        hashtag_id = _try_parse_uuid(value)
        if hashtag_id is not None:
            clauses.append(Hashtag.id == hashtag_id)
        else:
            clauses.append(Hashtag.tag == _normalize_hashtag(value))
    if not clauses:
        return None
    return or_(*clauses)


def _university_match_clause(
    values: list[str],
    *,
    university_id_column=None,
):
    """Match authors/users at ANY of the selected universities (OR).

    UUID values match ``university_id_column`` (preferred) or ``University.id``.
    Non-UUID values match ``University.name`` exactly (case-insensitive).
    """
    id_values: list[UUID] = []
    name_values: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        university_id = _try_parse_uuid(cleaned)
        if university_id is not None:
            id_values.append(university_id)
        else:
            name_values.append(cleaned)

    clauses = []
    if id_values:
        id_column = university_id_column if university_id_column is not None else University.id
        clauses.append(id_column.in_(id_values))
    for name in name_values:
        clauses.append(
            University.name.ilike(_escape_like_exact(name), escape="\\")
        )
    if not clauses:
        return None
    return or_(*clauses)


def _country_match_clause(
    values: list[str],
    *,
    country_id_column=None,
):
    """Match authors whose profile country is ANY of the selected values (OR).

    UUID values match ``country_id_column`` (preferred) or ``Country.id``.
    Non-UUID values match country name (exact, case-insensitive) or iso_code.
    """
    id_values: list[UUID] = []
    name_or_code_values: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        country_id = _try_parse_uuid(cleaned)
        if country_id is not None:
            id_values.append(country_id)
        else:
            name_or_code_values.append(cleaned)

    clauses = []
    if id_values:
        id_column = country_id_column if country_id_column is not None else Country.id
        clauses.append(id_column.in_(id_values))
    for term in name_or_code_values:
        escaped = _escape_like_exact(term)
        clauses.append(
            or_(
                func.lower(func.trim(Country.name)) == term.lower(),
                Country.iso_code.ilike(escaped, escape="\\"),
            )
        )
    if not clauses:
        return None
    return or_(*clauses)


def _edu_level_match_clause(author_profile, values: list[str]):
    """Match posts whose author has ANY of the selected education levels (OR).

    Selecting Bachelors|Masters returns authors with either level.
    Unknown filter values do not match any posts (fail closed).
    """
    cleaned = [value.strip() for value in values if value and str(value).strip()]
    if not cleaned:
        return None

    resolved_levels: list[str] = []
    seen: set[str] = set()
    for value in cleaned:
        resolved = _resolve_edu_level(value)
        if not resolved:
            continue
        key = resolved.lower()
        if key in seen:
            continue
        seen.add(key)
        resolved_levels.append(resolved)

    if not resolved_levels:
        return false()

    return or_(
        *[
            func.lower(func.trim(author_profile.edu_level)) == level.lower()
            for level in resolved_levels
        ]
    )


def _academic_interest_match_clause(author_profile, values: list[str]):
    """Match posts whose author has ANY of the selected academic interests (OR).

    Selecting A|B|C returns posts from authors who have A, or B, or C.
    Interests with no matching authors simply contribute no rows; others still match.
    """
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return None

    interest_id_values = [int(value) for value in cleaned if value.isdigit()]
    interest_name_values = [value for value in cleaned if not value.isdigit()]

    interest_predicates = []
    if interest_id_values:
        interest_predicates.append(AcademicInterest.id.in_(interest_id_values))
    for name in interest_name_values:
        interest_predicates.append(
            AcademicInterest.name.ilike(_escape_like_exact(name), escape="\\")
        )
    if not interest_predicates:
        return None

    # Single EXISTS: interest row matches ANY selected filter AND is on the author profile.
    return exists(
        select(1).where(
            AcademicInterest.is_active.is_(True),
            or_(*interest_predicates),
            _profile_interest_contains(author_profile, AcademicInterest.id),
        )
    )


def _program_field_match_clause(column, value: str | None):
    """Case-insensitive major/minor match (supports partial phrases)."""
    term = (value or "").strip()
    if not term:
        return None
    escaped = _escape_like_exact(term)
    return column.ilike(f"%{escaped}%", escape="\\")


def _build_search_filters(
    *,
    current_user_id: UUID,
    connected_author_ids: set[UUID],
    query: str | None,
    hashtag: str | list[str] | None,
    academic_interest: str | list[str] | None,
    university_name: str | list[str] | None,
    major: str | None,
    minor: str | None,
    country: str | list[str] | None,
    edu_level: str | list[str] | None,
    author_profile,
    author_user,
):
    filters = [
        Post.state == PostState.published,
        Post.author_user_id != current_user_id,
        *visible_user_filters(author_user),
    ]

    block_exists = exists(
        select(1).where(
            Block.is_active.is_(True),
            or_(
                and_(
                    Block.blocker_user_id == current_user_id,
                    Block.blocked_user_id == Post.author_user_id,
                ),
                and_(
                    Block.blocker_user_id == Post.author_user_id,
                    Block.blocked_user_id == current_user_id,
                ),
            ),
        )
    )
    filters.append(~block_exists)

    if connected_author_ids:
        connected_clause = Post.author_user_id.in_(connected_author_ids)
    else:
        connected_clause = false()

    filters.append(
        or_(
            author_profile.profile_visibility == ProfileVisibility.public,
            and_(
                author_profile.profile_visibility.in_(
                    [ProfileVisibility.private, ProfileVisibility.connections_only]
                ),
                connected_clause,
            ),
        )
    )

    if query and query.strip():
        term = query.strip()
        author_full_name = func.concat(
            func.coalesce(author_profile.first_name, ""),
            " ",
            func.coalesce(author_profile.last_name, ""),
        )
        query_clauses = [
            _word_boundary_match(Post.content["caption"].astext, term),
            _word_boundary_match(Post.content["content_html"].astext, term),
            _word_boundary_match(author_profile.first_name, term),
            _word_boundary_match(author_profile.last_name, term),
            _word_boundary_match(author_full_name, term),
        ]
        major_clause = _program_field_match_clause(author_profile.major, term)
        if major_clause is not None:
            query_clauses.append(major_clause)
        minor_clause = _program_field_match_clause(author_profile.minor, term)
        if minor_clause is not None:
            query_clauses.append(minor_clause)
        filters.append(or_(*query_clauses))

    hashtag_values = _split_filter_values(hashtag, split_whitespace=True)
    hashtag_clause = _hashtag_match_clause(hashtag_values)
    if hashtag_clause is not None:
        filters.append(
            exists(
                select(1)
                .select_from(PostHashtag)
                .join(Hashtag, Hashtag.id == PostHashtag.hashtag_id)
                .where(
                    PostHashtag.post_id == Post.id,
                    hashtag_clause,
                )
            )
        )

    # Author-profile academic interests (OR across selected interests).
    interest_clause = _academic_interest_match_clause(
        author_profile, _split_filter_values(academic_interest)
    )
    if interest_clause is not None:
        filters.append(interest_clause)

    university_clause = _university_match_clause(
        _split_filter_values(university_name),
        university_id_column=author_profile.university_id,
    )
    if university_clause is not None:
        filters.append(university_clause)

    major_clause = _program_field_match_clause(author_profile.major, major)
    if major_clause is not None:
        filters.append(major_clause)

    minor_clause = _program_field_match_clause(author_profile.minor, minor)
    if minor_clause is not None:
        filters.append(minor_clause)

    # Author-profile country (OR across selected countries).
    country_clause = _country_match_clause(
        _split_filter_values(country),
        country_id_column=author_profile.country_id,
    )
    if country_clause is not None:
        filters.append(country_clause)

    edu_clause = _edu_level_match_clause(author_profile, _split_filter_values(edu_level))
    if edu_clause is not None:
        filters.append(edu_clause)

    return filters


def _base_search_select(author_profile, author_user):
    return (
        select(Post)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .join(author_user, author_user.id == Post.author_user_id)
        .outerjoin(University, University.id == author_profile.university_id)
        .outerjoin(Country, Country.id == author_profile.country_id)
    )


async def count_search_posts(
    db: AsyncSession,
    current_user_id: UUID,
    *,
    query: str | None = None,
    hashtag: str | list[str] | None = None,
    academic_interest: str | list[str] | None = None,
    university_name: str | list[str] | None = None,
    major: str | None = None,
    minor: str | None = None,
    country: str | list[str] | None = None,
    edu_level: str | list[str] | None = None,
) -> int:
    author_profile = aliased(Profile, name="author_profile")
    author_user = aliased(User, name="author_user")
    connected_author_ids = await get_user_connections(db, current_user_id)
    filters = _build_search_filters(
        current_user_id=current_user_id,
        connected_author_ids=connected_author_ids,
        query=query,
        hashtag=hashtag,
        academic_interest=academic_interest,
        university_name=university_name,
        major=major,
        minor=minor,
        country=country,
        edu_level=edu_level,
        author_profile=author_profile,
        author_user=author_user,
    )

    stmt = (
        select(func.count(func.distinct(Post.id)))
        .select_from(Post)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .join(author_user, author_user.id == Post.author_user_id)
        .outerjoin(University, University.id == author_profile.university_id)
        .outerjoin(Country, Country.id == author_profile.country_id)
        .where(*filters)
    )
    return int((await db.execute(stmt)).scalar_one())


async def search_posts_with_details(
    db: AsyncSession,
    current_user_id: UUID,
    *,
    query: str | None = None,
    hashtag: str | list[str] | None = None,
    academic_interest: str | list[str] | None = None,
    university_name: str | list[str] | None = None,
    major: str | None = None,
    minor: str | None = None,
    country: str | list[str] | None = None,
    edu_level: str | list[str] | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple[Post, Profile | None, User | None, Profile | None]]:
    author_profile = aliased(Profile, name="author_profile")
    author_user = aliased(User, name="author_user")
    moderator_user = aliased(User, name="moderator_user")
    moderator_profile = aliased(Profile, name="moderator_profile")
    connected_author_ids = await get_user_connections(db, current_user_id)
    filters = _build_search_filters(
        current_user_id=current_user_id,
        connected_author_ids=connected_author_ids,
        query=query,
        hashtag=hashtag,
        academic_interest=academic_interest,
        university_name=university_name,
        major=major,
        minor=minor,
        country=country,
        edu_level=edu_level,
        author_profile=author_profile,
        author_user=author_user,
    )

    post_ids_stmt = (
        _base_search_select(author_profile, author_user)
        .where(*filters)
        .order_by(Post.created_at.desc())
        .offset(offset)
        .with_only_columns(Post.id)
    )
    if limit is not None:
        post_ids_stmt = post_ids_stmt.limit(limit)
    post_ids = list((await db.execute(post_ids_stmt)).scalars().all())
    if not post_ids:
        return []

    stmt = (
        select(Post, author_profile, moderator_user, moderator_profile)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .join(author_user, author_user.id == Post.author_user_id)
        .outerjoin(moderator_user, moderator_user.id == Post.moderator_id)
        .outerjoin(moderator_profile, moderator_profile.user_id == Post.moderator_id)
        .where(Post.id.in_(post_ids), *visible_user_filters(author_user))
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
        .order_by(Post.created_at.desc())
    )
    rows = list((await db.execute(stmt)).all())
    order = {post_id: index for index, post_id in enumerate(post_ids)}
    rows.sort(key=lambda row: order.get(row[0].id, 0))
    return rows
