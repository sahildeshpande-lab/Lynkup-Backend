from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.db_models import PostReaction, Repost, Bookmark
from apps.profiles.db_models import Profile
from common.enums import ReactionType


@dataclass(frozen=True)
class PostEngagementFlags:
    user_reactions: tuple[tuple[UUID, ReactionType], ...]
    reposted_post_ids: frozenset[UUID]
    bookmarked_post_ids: frozenset[UUID]

    @classmethod
    def empty(cls) -> PostEngagementFlags:
        return cls((), frozenset(), frozenset())

    def user_reaction_for(self, post_id: UUID) -> ReactionType | None:
        for pid, reaction_type in self.user_reactions:
            if pid == post_id:
                return reaction_type
        return None

    @property
    def liked_post_ids(self) -> frozenset[UUID]:
        return frozenset(pid for pid, _ in self.user_reactions)


async def fetch_post_engagement_flags(
    db: AsyncSession,
    user_id: UUID,
    post_ids: list[UUID],
) -> PostEngagementFlags:
    if not post_ids:
        return PostEngagementFlags.empty()

    reactions_stmt = select(PostReaction.post_id, PostReaction.reaction_type).where(
        PostReaction.user_id == user_id,
        PostReaction.post_id.in_(post_ids),
    )
    reaction_rows = (await db.execute(reactions_stmt)).all()
    user_reactions = tuple((row[0], row[1]) for row in reaction_rows)

    bookmarks_stmt = select(Bookmark.post_id).where(
        Bookmark.user_id == user_id,
        Bookmark.post_id.in_(post_ids),
    )
    bookmarked_post_ids = frozenset((await db.execute(bookmarks_stmt)).scalars().all())

    profile_id = (
        await db.execute(select(Profile.id).where(Profile.user_id == user_id))
    ).scalar_one_or_none()

    reposted_post_ids: frozenset[UUID] = frozenset()
    if profile_id is not None:
        repost_stmt = select(Repost.post_id).where(
            Repost.profile_id == profile_id,
            Repost.post_id.in_(post_ids),
        )
        reposted_post_ids = frozenset((await db.execute(repost_stmt)).scalars().all())

    return PostEngagementFlags(
        user_reactions=user_reactions,
        reposted_post_ids=reposted_post_ids,
        bookmarked_post_ids=bookmarked_post_ids,
    )
