from __future__ import annotations

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


def _normalize_hashtag(value: str) -> str:
    return value.strip().lstrip("#").lower()


def _try_parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value.strip())
    except (TypeError, ValueError, AttributeError):
        return None


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
    for level in EducationLevel:
        if level.value.lower() == raw.lower() or level.name.lower() == raw.lower():
            return level.value
    return raw


def _build_search_filters(
    *,
    current_user_id: UUID,
    connected_author_ids: set[UUID],
    query: str | None,
    hashtag: str | None,
    academic_interest: str | None,
    university_name: str | None,
    major: str | None,
    minor: str | None,
    country: str | None,
    edu_level: str | None,
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

    if hashtag and hashtag.strip():
        hashtag_id = _try_parse_uuid(hashtag)
        if hashtag_id is not None:
            tag_match = Hashtag.id == hashtag_id
        else:
            tag_match = Hashtag.tag == _normalize_hashtag(hashtag)
        filters.append(
            exists(
                select(1)
                .select_from(PostHashtag)
                .join(Hashtag, Hashtag.id == PostHashtag.hashtag_id)
                .where(
                    PostHashtag.post_id == Post.id,
                    tag_match,
                )
            )
        )

    if academic_interest and academic_interest.strip():
        interest_term = academic_interest.strip()
        if interest_term.isdigit():
            interest_id = int(interest_term)
            filters.append(
                cast(author_profile.profile_interests_id, JSONB).contains(
                    func.jsonb_build_array(interest_id)
                )
            )
        else:
            filters.append(
                exists(
                    select(1).where(
                        AcademicInterest.is_active.is_(True),
                        AcademicInterest.name.ilike(f"%{interest_term}%"),
                        cast(author_profile.profile_interests_id, JSONB).contains(
                            func.jsonb_build_array(AcademicInterest.id)
                        ),
                    )
                )
            )

    if university_name and university_name.strip():
        university_id = _try_parse_uuid(university_name)
        if university_id is not None:
            filters.append(University.id == university_id)
        else:
            filters.append(University.name.ilike(f"%{university_name.strip()}%"))

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

    if edu_level and edu_level.strip():
        resolved = _resolve_edu_level(edu_level)
        if resolved is not None:
            filters.append(author_profile.edu_level.ilike(resolved))

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
    hashtag: str | None = None,
    academic_interest: str | None = None,
    university_name: str | None = None,
    major: str | None = None,
    minor: str | None = None,
    country: str | None = None,
    edu_level: str | None = None,
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
    hashtag: str | None = None,
    academic_interest: str | None = None,
    university_name: str | None = None,
    major: str | None = None,
    minor: str | None = None,
    country: str | None = None,
    edu_level: str | None = None,
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
