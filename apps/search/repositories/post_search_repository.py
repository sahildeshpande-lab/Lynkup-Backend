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
from common.enums import PostState, ProfileVisibility
from sqlalchemy.dialects.postgresql import JSONB


def _normalize_hashtag(value: str) -> str:
    return value.strip().lstrip("#").lower()


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
    author_profile,
):
    filters = [Post.state == PostState.published]

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
        normalized_tag = _normalize_hashtag(hashtag)
        filters.append(
            exists(
                select(1)
                .select_from(PostHashtag)
                .join(Hashtag, Hashtag.id == PostHashtag.hashtag_id)
                .where(
                    PostHashtag.post_id == Post.id,
                    Hashtag.tag == normalized_tag,
                )
            )
        )

    if academic_interest and academic_interest.strip():
        interest_term = academic_interest.strip()
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
        filters.append(University.name.ilike(f"%{university_name.strip()}%"))

    if major and major.strip():
        filters.append(author_profile.major.ilike(f"%{major.strip()}%"))

    if minor and minor.strip():
        filters.append(author_profile.minor.ilike(f"%{minor.strip()}%"))

    if country and country.strip():
        filters.append(Country.name.ilike(f"%{country.strip()}%"))

    return filters


def _base_search_select(author_profile):
    return (
        select(Post)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
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
) -> int:
    author_profile = aliased(Profile, name="author_profile")
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
        author_profile=author_profile,
    )

    stmt = (
        select(func.count(func.distinct(Post.id)))
        .select_from(Post)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
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
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple[Post, Profile | None, User | None, Profile | None]]:
    author_profile = aliased(Profile, name="author_profile")
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
        author_profile=author_profile,
    )

    post_ids_stmt = (
        _base_search_select(author_profile)
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
        .outerjoin(moderator_user, moderator_user.id == Post.moderator_id)
        .outerjoin(moderator_profile, moderator_profile.user_id == Post.moderator_id)
        .where(Post.id.in_(post_ids))
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
        .order_by(Post.created_at.desc())
    )
    rows = list((await db.execute(stmt)).all())
    order = {post_id: index for index, post_id in enumerate(post_ids)}
    rows.sort(key=lambda row: order.get(row[0].id, 0))
    return rows
