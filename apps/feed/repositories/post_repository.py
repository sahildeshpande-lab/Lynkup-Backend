from __future__ import annotations

from collections.abc import Collection
from typing import Literal, Union
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.feed.db_models import Post
from common.enums import PostState


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


async def count_reviewed_posts_for_moderator(
    db: AsyncSession,
    moderator_id: UUID | None,
    status: Union[ReviewedPostStatus, None] = None,
) -> int:
    filters = _build_reviewed_posts_filter(moderator_id, status)
    stmt = (
        select(func.count(Post.id))
        .select_from(Post)
        .join(User, User.id == Post.author_user_id)
        .where(*filters)
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


async def fetch_reviewed_posts_for_moderator(
    db: AsyncSession,
    moderator_id: UUID | None,
    *,
    status: Union[ReviewedPostStatus, None] = None,
    offset: int = 0,
    limit: int | None = None,
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
        .order_by(Post.created_at.desc(), Post.id.desc())
        .offset(offset)
    )
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
    from sqlalchemy.orm import aliased, selectinload

    from apps.feed.db_models import PostAttachment
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
        .options(selectinload(Post.attachments).selectinload(PostAttachment.media_asset))
        .order_by(Post.created_at.desc(), Post.id.desc())
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    return list(result.all())
