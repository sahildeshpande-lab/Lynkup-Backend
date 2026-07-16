from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, case, false, func, or_, select, literal, union_all, UUID as SqlUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from apps.connections.db_models import Block
from apps.engagement.db_models import Repost
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


async def fetch_blocked_user_ids(db: AsyncSession, user_id: UUID) -> set[UUID]:
    stmt = select(Block.blocker_user_id, Block.blocked_user_id).where(
        Block.is_active == True,
        or_(
            Block.blocker_user_id == user_id,
            Block.blocked_user_id == user_id
        )
    )
    res = await db.execute(stmt)
    blocked_ids = set()
    for blocker, blocked in res.all():
        if blocker == user_id:
            blocked_ids.add(blocked)
        else:
            blocked_ids.add(blocker)
    return blocked_ids


async def count_feed_posts(
    db: AsyncSession,
    current_user_id: UUID,
    viewer_profile: Profile | None,
    connected_author_ids: set[UUID],
) -> int:
    blocked_user_ids = await fetch_blocked_user_ids(db, current_user_id)
    
    author_profile = aliased(Profile, name="author_profile")
    author_user = aliased(User, name="author_user")
    
    reposter_profile = aliased(Profile, name="reposter_profile")
    reposter_user = aliased(User, name="reposter_user")

    # Visibility filters
    connected_clause = Post.author_user_id.in_(connected_author_ids) if connected_author_ids else false()
    private_profile = author_profile.profile_visibility.in_([ProfileVisibility.private, ProfileVisibility.connections_only])
    public_profile = author_profile.profile_visibility == ProfileVisibility.public
    visibility_filter = or_(and_(private_profile, connected_clause), public_profile)

    posts_filters = [
        Post.state == PostState.published,
        Post.author_user_id != current_user_id,
        visibility_filter,
        *visible_user_filters(author_user),
    ]
    if blocked_user_ids:
        posts_filters.append(~Post.author_user_id.in_(blocked_user_ids))

    post_stmt = (
        select(Post.id.label("post_id"), literal(None, type_=SqlUUID).label("repost_id"))
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .join(author_user, author_user.id == Post.author_user_id)
        .where(*posts_filters)
    )

    reposter_connected_clause = reposter_profile.user_id.in_(connected_author_ids) if connected_author_ids else false()
    reposter_private = reposter_profile.profile_visibility.in_([ProfileVisibility.private, ProfileVisibility.connections_only])
    reposter_public = reposter_profile.profile_visibility == ProfileVisibility.public
    reposter_visibility = or_(and_(reposter_private, reposter_connected_clause), reposter_public)

    reposts_filters = [
        Post.state == PostState.published,
        reposter_profile.user_id != current_user_id,
        reposter_visibility,
        visibility_filter,
        *visible_user_filters(reposter_user),
        *visible_user_filters(author_user),
    ]
    if blocked_user_ids:
        reposts_filters.append(~reposter_profile.user_id.in_(blocked_user_ids))
        reposts_filters.append(~Post.author_user_id.in_(blocked_user_ids))

    repost_stmt = (
        select(Post.id.label("post_id"), Repost.id.label("repost_id"))
        .join(reposter_profile, reposter_profile.id == Repost.profile_id)
        .join(reposter_user, reposter_user.id == reposter_profile.user_id)
        .join(Post, Post.id == Repost.post_id)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .join(author_user, author_user.id == Post.author_user_id)
        .where(*reposts_filters)
    )

    union_stmt = union_all(post_stmt, repost_stmt)
    count_subquery = union_stmt.subquery("combined_feed")
    stmt = select(func.count()).select_from(count_subquery)
    return int((await db.execute(stmt)).scalar_one())


async def fetch_feed_posts(
    db: AsyncSession,
    current_user_id: UUID,
    viewer_profile: Profile | None,
    connected_author_ids: set[UUID],
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[dict]:
    blocked_user_ids = await fetch_blocked_user_ids(db, current_user_id)
    
    author_profile = aliased(Profile, name="author_profile")
    author_user = aliased(User, name="author_user")
    
    reposter_profile = aliased(Profile, name="reposter_profile")
    reposter_user = aliased(User, name="reposter_user")

    # Visibility filters
    connected_clause = Post.author_user_id.in_(connected_author_ids) if connected_author_ids else false()
    private_profile = author_profile.profile_visibility.in_([ProfileVisibility.private, ProfileVisibility.connections_only])
    public_profile = author_profile.profile_visibility == ProfileVisibility.public
    visibility_filter = or_(and_(private_profile, connected_clause), public_profile)

    posts_filters = [
        Post.state == PostState.published,
        Post.author_user_id != current_user_id,
        visibility_filter,
        *visible_user_filters(author_user),
    ]
    if blocked_user_ids:
        posts_filters.append(~Post.author_user_id.in_(blocked_user_ids))

    post_stmt = (
        select(
            Post.id.label("post_id"),
            literal(None, type_=SqlUUID).label("repost_id"),
            Post.created_at.label("event_time"),
            literal("post").label("event_type")
        )
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .join(author_user, author_user.id == Post.author_user_id)
        .where(*posts_filters)
    )

    reposter_connected_clause = reposter_profile.user_id.in_(connected_author_ids) if connected_author_ids else false()
    reposter_private = reposter_profile.profile_visibility.in_([ProfileVisibility.private, ProfileVisibility.connections_only])
    reposter_public = reposter_profile.profile_visibility == ProfileVisibility.public
    reposter_visibility = or_(and_(reposter_private, reposter_connected_clause), reposter_public)

    reposts_filters = [
        Post.state == PostState.published,
        reposter_profile.user_id != current_user_id,
        reposter_visibility,
        visibility_filter,
        *visible_user_filters(reposter_user),
        *visible_user_filters(author_user),
    ]
    if blocked_user_ids:
        reposts_filters.append(~reposter_profile.user_id.in_(blocked_user_ids))
        reposts_filters.append(~Post.author_user_id.in_(blocked_user_ids))

    repost_stmt = (
        select(
            Post.id.label("post_id"),
            Repost.id.label("repost_id"),
            Repost.created_at.label("event_time"),
            literal("repost").label("event_type")
        )
        .join(reposter_profile, reposter_profile.id == Repost.profile_id)
        .join(reposter_user, reposter_user.id == reposter_profile.user_id)
        .join(Post, Post.id == Repost.post_id)
        .join(author_profile, author_profile.user_id == Post.author_user_id)
        .join(author_user, author_user.id == Post.author_user_id)
        .where(*reposts_filters)
    )

    union_stmt = union_all(post_stmt, repost_stmt)
    paginated_subquery = union_stmt.subquery("combined_feed")
    feed_stmt = (
        select(
            paginated_subquery.c.post_id,
            paginated_subquery.c.repost_id,
            paginated_subquery.c.event_time,
            paginated_subquery.c.event_type
        )
        .order_by(paginated_subquery.c.event_time.desc())
    )

    if offset:
        feed_stmt = feed_stmt.offset(offset)
    if limit is not None:
        feed_stmt = feed_stmt.limit(limit)

    events = (await db.execute(feed_stmt)).all()
    if not events:
        return []

    post_ids = {row.post_id for row in events}
    repost_ids = {row.repost_id for row in events if row.repost_id}

    # Batch fetch posts + original author profiles
    post_author_profile = aliased(Profile, name="post_author_profile")
    post_stmt = (
        select(Post, post_author_profile)
        .join(post_author_profile, post_author_profile.user_id == Post.author_user_id)
        .where(Post.id.in_(post_ids))
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
    )
    post_results = await db.execute(post_stmt)
    posts_map = {post.id: (post, profile) for post, profile in post_results.all()}

    # Batch fetch reposts + reposter profiles
    reposts_map = {}
    if repost_ids:
        repost_stmt = (
            select(Repost, Profile)
            .join(Profile, Profile.id == Repost.profile_id)
            .where(Repost.id.in_(repost_ids))
        )
        repost_results = await db.execute(repost_stmt)
        reposts_map = {repost.id: (repost, profile) for repost, profile in repost_results.all()}

    # Reconstruct feed in correct timeline order
    results = []
    for row in events:
        post_data = posts_map.get(row.post_id)
        if not post_data:
            continue
        post, author_profile = post_data

        if row.event_type == "repost" and row.repost_id:
            repost_data = reposts_map.get(row.repost_id)
            if not repost_data:
                continue
            repost, reposter_profile = repost_data
            results.append({
                "post": post,
                "author_profile": author_profile,
                "is_reposted": True,
                "repost_id": repost.id,
                "reposted_by_profile": reposter_profile,
                "reposted_at": repost.created_at,
            })
        else:
            results.append({
                "post": post,
                "author_profile": author_profile,
                "is_reposted": False,
                "repost_id": None,
                "reposted_by_profile": None,
                "reposted_at": None,
            })

    return results
