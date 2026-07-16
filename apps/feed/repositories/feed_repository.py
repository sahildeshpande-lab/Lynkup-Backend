from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, case, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload
import sqlalchemy as sa

from apps.feed.db_models import Post, PostAttachment
from apps.feed.services.feed_scoring import viewer_has_relevance_criteria as _viewer_has_relevance_criteria
from apps.profiles.db_models import Profile
from apps.accounts.db_models import User
from common.enums import PostState, ProfileVisibility
from common.user_visibility import visible_user_filters


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
    author_user,
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
        *visible_user_filters(author_user),
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
    from apps.engagement.db_models import Repost
    from apps.connections.db_models import Block
    from sqlalchemy import literal, union_all, select, func, or_, and_

    # Get blocked user IDs (either direction)
    block_stmt = select(Block).where(
        Block.is_active == True,
        or_(
            Block.blocker_user_id == current_user_id,
            Block.blocked_user_id == current_user_id,
        ),
    )
    block_results = (await db.execute(block_stmt)).scalars().all()
    blocked_user_ids = {blk.blocker_user_id for blk in block_results} | {blk.blocked_user_id for blk in block_results}
    blocked_user_ids.discard(current_user_id)

    # Post query visibility filters
    author_profile = aliased(Profile, name="author_profile")
    author_user = aliased(User, name="author_user")

    if connected_author_ids:
        connected_clause = Post.author_user_id.in_(connected_author_ids)
    else:
        connected_clause = false()

    private_profile = author_profile.profile_visibility.in_(
        [ProfileVisibility.private, ProfileVisibility.connections_only]
    )
    public_profile = author_profile.profile_visibility == ProfileVisibility.public

    post_visibility_filter = or_(
        and_(private_profile, connected_clause),
        public_profile,
        Post.author_user_id == current_user_id,
    )

    post_filters = [
        Post.state == PostState.published,
        post_visibility_filter,
        *visible_user_filters(author_user),
    ]
    if blocked_user_ids:
        post_filters.append(Post.author_user_id.notin_(list(blocked_user_ids)))

    posts_query = select(
        literal("post").label("feed_type"),
        Post.id.label("activity_id"),
        Post.id.label("post_id"),
        literal(None, type_=sa.Uuid).label("reposter_profile_id"),
        Post.created_at.label("activity_created_at")
    ).join(
        author_profile, author_profile.user_id == Post.author_user_id
    ).join(
        author_user, author_user.id == Post.author_user_id
    ).where(*post_filters)

    # Repost query visibility filters
    reposter_profile = aliased(Profile, name="reposter_profile")
    reposter_user = aliased(User, name="reposter_user")
    orig_author_profile = aliased(Profile, name="orig_author_profile")
    orig_author_user = aliased(User, name="orig_author_user")

    reposter_private = reposter_profile.profile_visibility.in_(
        [ProfileVisibility.private, ProfileVisibility.connections_only]
    )
    reposter_public = reposter_profile.profile_visibility == ProfileVisibility.public

    if connected_author_ids:
        reposter_connected_clause = reposter_profile.user_id.in_(connected_author_ids)
    else:
        reposter_connected_clause = false()

    reposter_visibility_filter = or_(
        and_(reposter_private, reposter_connected_clause),
        reposter_public,
        reposter_profile.user_id == current_user_id,
    )

    repost_filters = [
        Post.state == PostState.published,
        reposter_visibility_filter,
        *visible_user_filters(reposter_user),
        *visible_user_filters(orig_author_user),
    ]
    if blocked_user_ids:
        repost_filters.append(reposter_profile.user_id.notin_(list(blocked_user_ids)))
        repost_filters.append(Post.author_user_id.notin_(list(blocked_user_ids)))

    reposts_query = select(
        literal("repost").label("feed_type"),
        Repost.id.label("activity_id"),
        Post.id.label("post_id"),
        reposter_profile.id.label("reposter_profile_id"),
        Repost.created_at.label("activity_created_at")
    ).join(
        Repost, Repost.post_id == Post.id
    ).join(
        reposter_profile, reposter_profile.id == Repost.profile_id
    ).join(
        reposter_user, reposter_user.id == reposter_profile.user_id
    ).join(
        orig_author_user, orig_author_user.id == Post.author_user_id
    ).where(*repost_filters)

    union_query = union_all(posts_query, reposts_query).subquery()
    count_stmt = select(func.count()).select_from(union_query)
    return int((await db.execute(count_stmt)).scalar_one())


async def fetch_feed_posts(
    db: AsyncSession,
    current_user_id: UUID,
    viewer_profile: Profile | None,
    connected_author_ids: set[UUID],
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple[str, UUID, Post, Profile | None, datetime]]:
    from apps.engagement.db_models import Repost
    from apps.connections.db_models import Block
    from sqlalchemy import literal, union_all, select, func, or_, and_

    # Get blocked user IDs (either direction)
    block_stmt = select(Block).where(
        Block.is_active == True,
        or_(
            Block.blocker_user_id == current_user_id,
            Block.blocked_user_id == current_user_id,
        ),
    )
    block_results = (await db.execute(block_stmt)).scalars().all()
    blocked_user_ids = {blk.blocker_user_id for blk in block_results} | {blk.blocked_user_id for blk in block_results}
    blocked_user_ids.discard(current_user_id)

    # Post query visibility filters
    author_profile = aliased(Profile, name="author_profile")
    author_user = aliased(User, name="author_user")

    if connected_author_ids:
        connected_clause = Post.author_user_id.in_(connected_author_ids)
    else:
        connected_clause = false()

    private_profile = author_profile.profile_visibility.in_(
        [ProfileVisibility.private, ProfileVisibility.connections_only]
    )
    public_profile = author_profile.profile_visibility == ProfileVisibility.public

    post_visibility_filter = or_(
        and_(private_profile, connected_clause),
        public_profile,
        Post.author_user_id == current_user_id,
    )

    post_filters = [
        Post.state == PostState.published,
        post_visibility_filter,
        *visible_user_filters(author_user),
    ]
    if blocked_user_ids:
        post_filters.append(Post.author_user_id.notin_(list(blocked_user_ids)))

    posts_query = select(
        literal("post").label("feed_type"),
        Post.id.label("activity_id"),
        Post.id.label("post_id"),
        literal(None, type_=sa.Uuid).label("reposter_profile_id"),
        Post.created_at.label("activity_created_at")
    ).join(
        author_profile, author_profile.user_id == Post.author_user_id
    ).join(
        author_user, author_user.id == Post.author_user_id
    ).where(*post_filters)

    # Repost query visibility filters
    reposter_profile = aliased(Profile, name="reposter_profile")
    reposter_user = aliased(User, name="reposter_user")
    orig_author_profile = aliased(Profile, name="orig_author_profile")
    orig_author_user = aliased(User, name="orig_author_user")

    reposter_private = reposter_profile.profile_visibility.in_(
        [ProfileVisibility.private, ProfileVisibility.connections_only]
    )
    reposter_public = reposter_profile.profile_visibility == ProfileVisibility.public

    if connected_author_ids:
        reposter_connected_clause = reposter_profile.user_id.in_(connected_author_ids)
    else:
        reposter_connected_clause = false()

    reposter_visibility_filter = or_(
        and_(reposter_private, reposter_connected_clause),
        reposter_public,
        reposter_profile.user_id == current_user_id,
    )

    repost_filters = [
        Post.state == PostState.published,
        reposter_visibility_filter,
        *visible_user_filters(reposter_user),
        *visible_user_filters(orig_author_user),
    ]
    if blocked_user_ids:
        repost_filters.append(reposter_profile.user_id.notin_(list(blocked_user_ids)))
        repost_filters.append(Post.author_user_id.notin_(list(blocked_user_ids)))

    reposts_query = select(
        literal("repost").label("feed_type"),
        Repost.id.label("activity_id"),
        Post.id.label("post_id"),
        reposter_profile.id.label("reposter_profile_id"),
        Repost.created_at.label("activity_created_at")
    ).join(
        Repost, Repost.post_id == Post.id
    ).join(
        reposter_profile, reposter_profile.id == Repost.profile_id
    ).join(
        reposter_user, reposter_user.id == reposter_profile.user_id
    ).join(
        orig_author_user, orig_author_user.id == Post.author_user_id
    ).where(*repost_filters)

    union_query = union_all(posts_query, reposts_query).subquery()
    stmt = select(
        union_query.c.feed_type,
        union_query.c.activity_id,
        union_query.c.post_id,
        union_query.c.reposter_profile_id,
        union_query.c.activity_created_at,
    ).order_by(union_query.c.activity_created_at.desc())

    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).all()
    if not rows:
        return []

    # Batch load Posts and Profiles to prevent N+1 queries
    post_ids = [r.post_id for r in rows]
    reposter_profile_ids = [r.reposter_profile_id for r in rows if r.reposter_profile_id is not None]

    posts_dict = {}
    if post_ids:
        post_author_profile = aliased(Profile, name="post_author_profile")
        posts_stmt = (
            select(Post, post_author_profile)
            .join(post_author_profile, post_author_profile.user_id == Post.author_user_id)
            .where(Post.id.in_(post_ids))
            .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
        )
        posts_results = (await db.execute(posts_stmt)).all()
        for post, author_profile in posts_results:
            post._author_profile = author_profile
            posts_dict[post.id] = post

    reposter_profiles_dict = {}
    if reposter_profile_ids:
        profiles_stmt = select(Profile).where(Profile.id.in_(reposter_profile_ids))
        profiles_results = (await db.execute(profiles_stmt)).scalars().all()
        for profile in profiles_results:
            reposter_profiles_dict[profile.id] = profile

    results = []
    for r in rows:
        post = posts_dict.get(r.post_id)
        if post:
            reposter_profile = reposter_profiles_dict.get(r.reposter_profile_id)
            results.append((r.feed_type, r.activity_id, post, reposter_profile, r.activity_created_at))

    return results
