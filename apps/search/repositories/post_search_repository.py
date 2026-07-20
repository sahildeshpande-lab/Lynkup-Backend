from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import and_, cast, exists, false, func, or_, select
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
    return raw


def _profile_interest_contains(author_profile, interest_id: int):
    """Match interest ids stored as JSON numbers or JSON strings."""
    interests_json = cast(author_profile.profile_interests_id, JSONB)
    return or_(
        interests_json.contains(func.jsonb_build_array(interest_id)),
        interests_json.contains(func.jsonb_build_array(str(interest_id))),
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


def _university_match_clause(values: list[str]):
    clauses = []
    for value in values:
        university_id = _try_parse_uuid(value)
        if university_id is not None:
            clauses.append(University.id == university_id)
        else:
            clauses.append(University.name.ilike(value))
    if not clauses:
        return None
    return or_(*clauses)


def _edu_level_match_clause(author_profile, values: list[str]):
    resolved_levels = []
    for value in values:
        resolved = _resolve_edu_level(value)
        if resolved:
            resolved_levels.append(resolved)
    if not resolved_levels:
        return None
    return or_(*[author_profile.edu_level.ilike(level) for level in resolved_levels])


def _academic_interest_match_clause(author_profile, values: list[str]):
    clauses = []
    for value in values:
        if value.isdigit():
            # Require a real academic_interests row so edu-level ids don't false-match.
            clauses.append(
                exists(
                    select(1).where(
                        AcademicInterest.id == int(value),
                        AcademicInterest.is_active.is_(True),
                        _profile_interest_contains(author_profile, AcademicInterest.id),
                    )
                )
            )
            continue
        # Exact case-insensitive name match to avoid substring false positives.
        clauses.append(
            exists(
                select(1).where(
                    AcademicInterest.is_active.is_(True),
                    AcademicInterest.name.ilike(value),
                    _profile_interest_contains(author_profile, AcademicInterest.id),
                )
            )
        )
    if not clauses:
        return None
    return or_(*clauses)


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
    country: str | None,
    edu_level: str | list[str] | None,
    author_profile,
    author_user,
):
    filters = [
        Post.state == PostState.published,
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
            Post.author_user_id == current_user_id,
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
        filters.append(
            or_(
                Post.content["caption"].astext.ilike(f"%{term}%"),
                Post.content["content_html"].astext.ilike(f"%{term}%"),
            )
        )

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

    interest_clause = _academic_interest_match_clause(
        author_profile, _split_filter_values(academic_interest)
    )
    if interest_clause is not None:
        filters.append(interest_clause)

    university_clause = _university_match_clause(_split_filter_values(university_name))
    if university_clause is not None:
        filters.append(university_clause)

    if major and major.strip():
        filters.append(author_profile.major.ilike(f"%{major.strip()}%"))

    if minor and minor.strip():
        filters.append(author_profile.minor.ilike(f"%{minor.strip()}%"))

    if country and country.strip():
        country_term = country.strip()
        country_id = _try_parse_uuid(country_term)
        if country_id is not None:
            filters.append(Country.id == country_id)
        else:
            filters.append(
                or_(
                    Country.name.ilike(f"%{country_term}%"),
                    Country.iso_code.ilike(country_term),
                )
            )

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
    country: str | None = None,
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
    country: str | None = None,
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
