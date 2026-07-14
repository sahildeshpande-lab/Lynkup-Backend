from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from apps.feed.db_models import Post, PostAttachment
from apps.feed.services.feed_scoring import viewer_has_relevance_criteria as _viewer_has_relevance_criteria
from apps.profiles.db_models import Profile
from common.enums import PostState, ProfileVisibility


def _normalized_string_match(column, value: str | None):
    normalized = (value or "").strip().lower()
    if not normalized:
        return false()
    return and_(
        column.isnot(None),
        func.lower(func.trim(column)) == normalized,
    )


def _university_match(column, university_id: UUID | None):
    if university_id is None:
        return false()
    return and_(column.isnot(None), column == university_id)


def _build_feed_match_expressions(
    author_profile,
    *,
    viewer_major: str | None,
    viewer_minor: str | None,
    viewer_university_id: UUID | None,
):
    major_match = _normalized_string_match(author_profile.major, viewer_major)
    minor_match = _normalized_string_match(author_profile.minor, viewer_minor)
    university_match = _university_match(author_profile.university_id, viewer_university_id)
    relevance_match = or_(major_match, minor_match, university_match)
    return relevance_match


def _build_feed_filters(
    *,
    current_user_id: UUID,
    author_profile,
    connected_author_ids: set[UUID],
):
    if connected_author_ids:
        connected_clause = Post.author_user_id.in_(connected_author_ids)
    else:
        connected_clause = false()

    private_profile = author_profile.profile_visibility.in_(
        [ProfileVisibility.private, ProfileVisibility.connections_only]
    )
    public_profile = author_profile.profile_visibility == ProfileVisibility.public

    visibility_filter = or_(
        and_(private_profile, connected_clause),
        public_profile,
    )

    return [
        Post.state == PostState.published,
        Post.author_user_id != current_user_id,
        visibility_filter,
    ]


def _viewer_profile_fields(viewer_profile: Profile | None):
    if viewer_profile is None:
        return None, None, None
    return viewer_profile.major, viewer_profile.minor, viewer_profile.university_id


async def fetch_viewer_profile(db: AsyncSession, user_id: UUID) -> Profile | None:
    stmt = select(Profile).where(Profile.user_id == user_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def count_feed_posts(
    db: AsyncSession,
    current_user_id: UUID,
    viewer_profile: Profile | None,
    connected_author_ids: set[UUID],
) -> int:
    author_profile = aliased(Profile, name="author_profile")
    filters = _build_feed_filters(
        current_user_id=current_user_id,
        author_profile=author_profile,
        connected_author_ids=connected_author_ids,
    )
    stmt = (
        select(func.count(Post.id))
        .select_from(Post)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .where(*filters)
    )
    return int((await db.execute(stmt)).scalar_one())


async def fetch_feed_posts(
    db: AsyncSession,
    current_user_id: UUID,
    viewer_profile: Profile | None,
    connected_author_ids: set[UUID],
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple[Post, Profile]]:
    author_profile = aliased(Profile, name="author_profile")
    viewer_major, viewer_minor, viewer_university_id = _viewer_profile_fields(viewer_profile)
    filters = _build_feed_filters(
        current_user_id=current_user_id,
        author_profile=author_profile,
        connected_author_ids=connected_author_ids,
    )

    post_ids_stmt = (
        select(Post.id)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .where(*filters)
    )

    has_relevance_criteria = _viewer_has_relevance_criteria(
        viewer_major=viewer_major,
        viewer_minor=viewer_minor,
        viewer_university_id=viewer_university_id,
    )
    if has_relevance_criteria:
        relevance_match = _build_feed_match_expressions(
            author_profile,
            viewer_major=viewer_major,
            viewer_minor=viewer_minor,
            viewer_university_id=viewer_university_id,
        )
        relevance_rank = case((relevance_match, 1), else_=0)
        post_ids_stmt = post_ids_stmt.order_by(relevance_rank.desc(), Post.created_at.desc())
    else:
        post_ids_stmt = post_ids_stmt.order_by(Post.created_at.desc())

    post_ids_stmt = post_ids_stmt.offset(offset)
    if limit is not None:
        post_ids_stmt = post_ids_stmt.limit(limit)

    post_ids = list((await db.execute(post_ids_stmt)).scalars().all())
    if not post_ids:
        return []

    stmt = (
        select(Post, author_profile)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .where(Post.id.in_(post_ids))
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
    )
    rows = list((await db.execute(stmt)).all())
    order = {post_id: index for index, post_id in enumerate(post_ids)}
    rows.sort(key=lambda row: order.get(row[0].id, 0))
    return rows
