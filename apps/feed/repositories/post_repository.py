from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Union
from uuid import UUID

from sqlalchemy import and_, cast, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.feed.db_models import Post
from common.enums import (
    FEED_VISIBLE_POST_STATES,
    PostState,
    ReviewedPostOrder,
    ReviewedPostSort,
)


def _post_list_hydration_load_options():
    """
    Loader options for GET /posts list hydration.

    Post/User mappers default many relationships to lazy='selectin', which
    triggers hidden queries during result.all(). List responses only need
    attachments + media assets on Post; author/moderator profiles come from
    explicit joins in the same statement.
    """
    from sqlalchemy.orm import lazyload, noload, selectinload

    from apps.feed.db_models import PostAttachment
    from apps.feed.db_models.media_asset_db_model import MediaAsset

    return (
        lazyload("*"),
        selectinload(Post.attachments)
        .selectinload(PostAttachment.media_asset)
        .noload(MediaAsset.posts),
    )


def _post_state_filter(state: PostState | Collection[PostState]):
    """Build a Post.state filter for one state or a set of states."""
    if isinstance(state, Collection) and not isinstance(state, (str, PostState)):
        states = list(state)
        if len(states) == 1:
            return Post.state == states[0]
        return Post.state.in_(states)
    return Post.state == state


# Public-facing status values accepted by the reviewed-posts endpoint.
# ``published`` includes reinstate (public-visible set). ``flagged`` includes
# ``processing`` (re-opened for review). Other statuses map 1:1 to Post.state.
ReviewedPostStatus = Literal[
    "published", "flagged", "rejected", "reinstate", "escalate", "processing"
]

_REVIEWED_STATUS_TO_STATES: dict[str, tuple[PostState, ...]] = {
    "published": (PostState.published, PostState.reinstate),
    "flagged": (PostState.flagged, PostState.processing),
    "rejected": (PostState.rejected,),
    "reinstate": (PostState.reinstate,),
    "escalate": (PostState.escalate,),
    "processing": (PostState.processing,),
}

# Backward-compatible alias used by repair helpers (single primary state).
_REVIEWED_STATUS_TO_STATE: dict[str, PostState] = {
    status: states[0] for status, states in _REVIEWED_STATUS_TO_STATES.items()
}

# Default status when no filter is supplied → same as status=published.
_DEFAULT_REVIEWED_STATUS = "published"


def _reviewed_states_for_status(
    status: Union[ReviewedPostStatus, None],
) -> tuple[PostState, ...]:
    if status is None:
        return _REVIEWED_STATUS_TO_STATES[_DEFAULT_REVIEWED_STATUS]
    return _REVIEWED_STATUS_TO_STATES.get(
        status, _REVIEWED_STATUS_TO_STATES[_DEFAULT_REVIEWED_STATUS]
    )


def _build_reviewed_posts_filter(
    moderator_id: UUID | None,
    status: Union[ReviewedPostStatus, None],
):
    from common.user_visibility import visible_user_filters

    target_states = _reviewed_states_for_status(status)
    # Filter by state only. User-published posts (state=published,
    # is_moderator_reviewed=False) must appear so the moderator can act on them.
    # is_moderator_reviewed is informational metadata, not a visibility gate.
    # Hide posts from suspended / banned / deleting authors.
    filters = [_post_state_filter(target_states), *visible_user_filters(User)]
    if moderator_id is not None:
        filters.append(Post.moderator_id == moderator_id)
    return filters


_UPDATED_AT_SORTS = frozenset(
    {ReviewedPostSort.updated_at, ReviewedPostSort.latest_post}
)


def resolve_reviewed_posts_sort(
    sort: ReviewedPostSort | None,
    status: Union[ReviewedPostStatus, None],
) -> ReviewedPostSort:
    """Default sort column when the client omits ``sort``.

    Flagged (and processing) uses ``updated_at``; published / rejected / others
    use ``created_at``.
    """
    if sort is not None:
        return sort
    if status == "flagged":
        return ReviewedPostSort.updated_at
    return ReviewedPostSort.created_at


def _reviewed_posts_order_by(
    sort: ReviewedPostSort | None = None,
    order: ReviewedPostOrder | None = None,
):
    """ORDER BY for GET /admin/posts/reviewed.

    ``created_at`` (default): student posts / published / rejected.
    ``updated_at`` (and legacy ``latest_post``): flagged tab, last student edit.
    ``order`` defaults to ``desc`` (newest / latest first).
    """
    descending = order != ReviewedPostOrder.asc
    column = Post.updated_at if sort in _UPDATED_AT_SORTS else Post.created_at
    if descending:
        return (column.desc(), Post.id.desc())
    return (column.asc(), Post.id.asc())


def _reviewed_posts_search_clause(search: str | None, profile):
    """Match author first/last/full name or post caption/HTML content."""
    term = (search or "").strip()
    if not term:
        return None
    pattern = f"%{term}%"
    return or_(
        profile.first_name.ilike(pattern),
        profile.last_name.ilike(pattern),
        func.concat(profile.first_name, " ", profile.last_name).ilike(pattern),
        Post.content["caption"].astext.ilike(pattern),
        Post.content["content_html"].astext.ilike(pattern),
    )


async def count_reviewed_posts_for_moderator(
    db: AsyncSession,
    moderator_id: UUID | None,
    status: Union[ReviewedPostStatus, None] = None,
    search: str | None = None,
) -> int:
    from apps.profiles.db_models import Profile

    filters = _build_reviewed_posts_filter(moderator_id, status)
    stmt = (
        select(func.count(Post.id))
        .select_from(Post)
        .join(User, User.id == Post.author_user_id)
        .where(*filters)
    )
    search_clause = _reviewed_posts_search_clause(search, Profile)
    if search_clause is not None:
        stmt = stmt.outerjoin(Profile, Profile.user_id == Post.author_user_id).where(
            search_clause
        )
    return int((await db.execute(stmt)).scalar_one())


async def count_reviewed_posts_summary_by_state(
    db: AsyncSession,
    moderator_id: UUID | None,
) -> dict[str, int]:
    from common.user_visibility import visible_user_filters

    # processing → flagged tab; reinstate → published tab (matches list filters).
    # reinstate is also kept as its own summary key for the reinstate tab.
    rollup_to_status = {
        PostState.published: "published",
        PostState.reinstate: "published",
        PostState.flagged: "flagged",
        PostState.processing: "flagged",
        PostState.rejected: "rejected",
        PostState.escalate: "escalate",
    }
    summary_keys = ("published", "flagged", "rejected", "reinstate", "escalate")
    stmt = (
        select(Post.state, func.count(Post.id))
        .select_from(Post)
        .join(User, User.id == Post.author_user_id)
        .where(
            Post.state.in_(list(rollup_to_status.keys())),
            *visible_user_filters(User),
        )
    )
    if moderator_id is not None:
        stmt = stmt.where(Post.moderator_id == moderator_id)
    stmt = stmt.group_by(Post.state)

    result = await db.execute(stmt)
    counts = {key: 0 for key in summary_keys}
    for state, count in result.all():
        status_key = rollup_to_status.get(state)
        if status_key is not None:
            counts[status_key] += count
        if state == PostState.reinstate:
            counts["reinstate"] += count
    return counts


async def count_user_posts_summary_by_state(
    db: AsyncSession,
    *,
    user_id: UUID | None,
) -> dict[str, int]:
    """
    Count a user's posts for list-tab summary chips.

    Rollups match GET /posts filters:
    - published includes reinstate
    - flagged includes processing
    - reinstate is also returned as its own key
    """
    from common.user_visibility import visible_user_filters

    rollup_to_status = {
        PostState.published: "published",
        PostState.reinstate: "published",
        PostState.flagged: "flagged",
        PostState.processing: "flagged",
        PostState.rejected: "rejected",
    }
    summary_keys = ("published", "flagged", "rejected", "reinstate")
    filters = [
        Post.state.in_(list(rollup_to_status.keys())),
        *visible_user_filters(User),
    ]
    if user_id is not None:
        filters.append(Post.author_user_id == user_id)

    stmt = (
        select(Post.state, func.count(Post.id))
        .select_from(Post)
        .join(User, User.id == Post.author_user_id)
        .where(*filters)
        .group_by(Post.state)
    )
    result = await db.execute(stmt)
    counts = {key: 0 for key in summary_keys}
    for state, count in result.all():
        status_key = rollup_to_status.get(state)
        if status_key is not None:
            counts[status_key] += count
        if state == PostState.reinstate:
            counts["reinstate"] += count
    return counts


async def fetch_reviewed_posts_for_moderator(
    db: AsyncSession,
    moderator_id: UUID | None,
    *,
    status: Union[ReviewedPostStatus, None] = None,
    offset: int = 0,
    limit: int | None = None,
    search: str | None = None,
    sort: ReviewedPostSort | None = None,
    order: ReviewedPostOrder | None = None,
) -> list[tuple[Post, object | None, object | None, object | None]]:
    from apps.profiles.db_models import Profile
    from sqlalchemy.orm import aliased, selectinload

    from apps.feed.db_models import PostAttachment
    from common.user_visibility import visible_user_filters

    AuthorUser = aliased(User, name="author_user")
    AuthorProfile = aliased(Profile, name="author_profile")
    ModeratorUser = aliased(User, name="moderator_user")
    ModeratorProfile = aliased(Profile, name="moderator_profile")

    target_states = _reviewed_states_for_status(status)
    filters = [_post_state_filter(target_states), *visible_user_filters(AuthorUser)]
    if moderator_id is not None:
        filters.append(Post.moderator_id == moderator_id)

    stmt = (
        select(Post, AuthorProfile, ModeratorUser, ModeratorProfile)
        .join(AuthorUser, AuthorUser.id == Post.author_user_id)
        .outerjoin(AuthorProfile, AuthorProfile.user_id == Post.author_user_id)
        .outerjoin(ModeratorUser, ModeratorUser.id == Post.moderator_id)
        .outerjoin(ModeratorProfile, ModeratorProfile.user_id == Post.moderator_id)
        .where(*filters)
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
        .order_by(*_reviewed_posts_order_by(sort, order))
        .offset(offset)
    )
    search_clause = _reviewed_posts_search_clause(search, AuthorProfile)
    if search_clause is not None:
        stmt = stmt.where(search_clause)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return list(result.all())


async def user_exists(db: AsyncSession, user_id: UUID) -> bool:
    """Return whether a user exists."""
    result = await db.execute(select(User.id).where(User.id == user_id))
    return result.scalar_one_or_none() is not None


async def count_posts_by_state(
    db: AsyncSession,
    *,
    state: PostState | Collection[PostState],
    user_id: UUID | None = None,
) -> int:
    """Count posts filtered by state(s) and optional author."""
    from common.user_visibility import visible_user_filters

    filters = [_post_state_filter(state), *visible_user_filters(User)]
    if user_id is not None:
        filters.append(Post.author_user_id == user_id)
    stmt = (
        select(func.count(Post.id))
        .select_from(Post)
        .join(User, User.id == Post.author_user_id)
        .where(*filters)
    )
    return int((await db.execute(stmt)).scalar_one())


async def fetch_posts_by_state(
    db: AsyncSession,
    *,
    state: PostState | Collection[PostState],
    user_id: UUID | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> list[Post]:
    """Fetch posts filtered by state(s) and optional author, newest first."""
    from common.user_visibility import visible_user_filters

    filters = [_post_state_filter(state), *visible_user_filters(User)]
    if user_id is not None:
        filters.append(Post.author_user_id == user_id)

    stmt = (
        select(Post)
        .join(User, User.id == Post.author_user_id)
        .where(*filters)
        .order_by(Post.created_at.desc(), Post.id.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def fetch_posts_by_state_with_details(
    db: AsyncSession,
    *,
    state: PostState | Collection[PostState],
    user_id: UUID | None = None,
    offset: int = 0,
    limit: int | None = None,
) -> list[tuple[Post, object | None, object | None, object | None]]:
    """Fetch posts with author profile and assigned moderator details."""
    from sqlalchemy.orm import aliased

    from apps.profiles.db_models import Profile
    from common.user_visibility import visible_user_filters

    AuthorProfile = aliased(Profile, name="author_profile")
    AuthorUser = aliased(User, name="author_user")
    ModeratorUser = aliased(User, name="moderator_user")
    ModeratorProfile = aliased(Profile, name="moderator_profile")

    filters = [_post_state_filter(state), *visible_user_filters(AuthorUser)]
    if user_id is not None:
        filters.append(Post.author_user_id == user_id)

    stmt = (
        select(Post, AuthorProfile, ModeratorUser, ModeratorProfile)
        .join(AuthorUser, AuthorUser.id == Post.author_user_id)
        .outerjoin(AuthorProfile, AuthorProfile.user_id == Post.author_user_id)
        .outerjoin(ModeratorUser, ModeratorUser.id == Post.moderator_id)
        .outerjoin(ModeratorProfile, ModeratorProfile.user_id == Post.moderator_id)
        .where(*filters)
        .options(*_post_list_hydration_load_options())
        .order_by(Post.created_at.desc(), Post.id.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    return list(result.all())


@dataclass(frozen=True)
class UserTimelineEvent:
    """One row in a user's profile timeline (authored post or repost)."""

    post_id: UUID
    repost_id: UUID | None
    event_type: Literal["post", "repost"]
    sort_at: datetime


@dataclass(frozen=True)
class AuthoredTimelineItem:
    kind: Literal["post"]
    post: Post
    author_profile: object | None
    moderator_user: object | None
    moderator_profile: object | None


@dataclass(frozen=True)
class RepostTimelineItem:
    kind: Literal["repost"]
    repost: object
    post: Post
    author_profile: object | None
    reposter_profile: object | None


UserTimelineItem = AuthoredTimelineItem | RepostTimelineItem


def _normalize_timeline_states(
    state: PostState | Collection[PostState],
) -> list[PostState]:
    if isinstance(state, Collection) and not isinstance(state, (str, PostState)):
        return list(state)
    return [state]  # type: ignore[list-item]


def _user_posts_timeline_subquery(
    *,
    user_id: UUID,
    state: PostState | Collection[PostState],
    include_reposts: bool,
):
    """UNION of authored posts and (optionally) the user's active reposts."""
    from apps.engagement.db_models import Repost
    from common.user_visibility import visible_user_filters

    states = _normalize_timeline_states(state)
    authored = (
        select(
            Post.id.label("post_id"),
            cast(literal(None), Post.id.type).label("repost_id"),
            Post.created_at.label("sort_at"),
            literal("post").label("event_type"),
        )
        .join(User, User.id == Post.author_user_id)
        .where(
            Post.author_user_id == user_id,
            _post_state_filter(states),
            *visible_user_filters(User),
        )
    )

    if not include_reposts:
        return authored.subquery()

    reposts = (
        select(
            Post.id.label("post_id"),
            Repost.id.label("repost_id"),
            Repost.created_at.label("sort_at"),
            literal("repost").label("event_type"),
        )
        .select_from(Repost)
        .join(Post, Post.id == Repost.post_id)
        .join(User, User.id == Post.author_user_id)
        .where(
            Repost.user_id == user_id,
            Repost.is_deleted.is_(False),
            Post.state.in_(list(FEED_VISIBLE_POST_STATES)),
            *visible_user_filters(User),
        )
    )
    return union_all(authored, reposts).subquery()


async def count_user_posts_timeline(
    db: AsyncSession,
    *,
    user_id: UUID,
    state: PostState | Collection[PostState],
    include_reposts: bool = False,
) -> int:
    """Count authored posts (+ optional feed-visible reposts) for timeline totals."""
    timeline = _user_posts_timeline_subquery(
        user_id=user_id,
        state=state,
        include_reposts=include_reposts,
    )
    stmt = select(func.count()).select_from(timeline)
    return int((await db.execute(stmt)).scalar_one())


def _timeline_keyset_filter(timeline, cursor: dict) -> object:
    """Keyset predicate for ORDER BY sort_at DESC, post_id DESC, repost_id DESC NULLS LAST."""
    from apps.feed.services.user_posts_cursor import repost_id_for_keyset

    cursor_sort_at = cursor["sort_at"]
    cursor_post_id = cursor["post_id"]
    cursor_repost_id = repost_id_for_keyset(cursor.get("repost_id"))
    null_sentinel = repost_id_for_keyset(None)
    event_repost = func.coalesce(timeline.c.repost_id, literal(null_sentinel))

    return or_(
        timeline.c.sort_at < cursor_sort_at,
        and_(
            timeline.c.sort_at == cursor_sort_at,
            timeline.c.post_id < cursor_post_id,
        ),
        and_(
            timeline.c.sort_at == cursor_sort_at,
            timeline.c.post_id == cursor_post_id,
            event_repost < cursor_repost_id,
        ),
    )


async def fetch_user_posts_timeline_events(
    db: AsyncSession,
    *,
    user_id: UUID,
    state: PostState | Collection[PostState],
    include_reposts: bool = False,
    offset: int = 0,
    limit: int | None = None,
    cursor: dict | None = None,
) -> list[UserTimelineEvent]:
    """Fetch the user's post+repost timeline (newest first).

    When ``cursor`` is provided, uses keyset pagination. Otherwise falls back to
    OFFSET for legacy page-number requests.
    """
    timeline = _user_posts_timeline_subquery(
        user_id=user_id,
        state=state,
        include_reposts=include_reposts,
    )
    stmt = select(
        timeline.c.post_id,
        timeline.c.repost_id,
        timeline.c.sort_at,
        timeline.c.event_type,
    ).order_by(
        timeline.c.sort_at.desc(),
        timeline.c.post_id.desc(),
        timeline.c.repost_id.desc().nulls_last(),
    )
    if cursor is not None:
        stmt = stmt.where(_timeline_keyset_filter(timeline, cursor))
    elif offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).all()
    events: list[UserTimelineEvent] = []
    for post_id, repost_id, sort_at, event_type in rows:
        kind: Literal["post", "repost"] = (
            "repost" if str(event_type) == "repost" else "post"
        )
        events.append(
            UserTimelineEvent(
                post_id=post_id,
                repost_id=repost_id,
                event_type=kind,
                sort_at=sort_at,
            )
        )
    return events


async def hydrate_user_posts_timeline(
    db: AsyncSession,
    events: Sequence[UserTimelineEvent],
    *,
    reposter_user_id: UUID,
) -> list[UserTimelineItem]:
    """Load post/repost details for a timeline page, preserving event order."""
    if not events:
        return []

    from apps.feed.repositories.feed_repost_hydration import hydrate_feed_reposts_raw
    from apps.feed.repositories.timeline_post_hydration import hydrate_timeline_posts_raw

    post_ids = list({event.post_id for event in events})
    repost_ids = [event.repost_id for event in events if event.repost_id is not None]

    post_rows = await hydrate_timeline_posts_raw(db, post_ids)
    repost_hydration: dict = {}
    if repost_ids:
        repost_hydration = await hydrate_feed_reposts_raw(db, repost_ids)

    items: list[UserTimelineItem] = []
    for event in events:
        packed = post_rows.get(event.post_id)
        if packed is None:
            continue
        post, author_profile, mod_user, mod_profile = packed
        if event.event_type == "repost" and event.repost_id is not None:
            packed_repost = repost_hydration.get(event.repost_id)
            if packed_repost is None:
                continue
            repost, reposter_profile = packed_repost
            items.append(
                RepostTimelineItem(
                    kind="repost",
                    repost=repost,
                    post=post,
                    author_profile=author_profile,
                    reposter_profile=reposter_profile,
                )
            )
        else:
            items.append(
                AuthoredTimelineItem(
                    kind="post",
                    post=post,
                    author_profile=author_profile,
                    moderator_user=mod_user,
                    moderator_profile=mod_profile,
                )
            )
    return items
