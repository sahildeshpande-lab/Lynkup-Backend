from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from common.enums import ReactionType

_FETCH_POST_ENGAGEMENT_FLAGS_SQL = """
WITH requested_posts AS (
    SELECT DISTINCT unnest(CAST(:post_ids AS uuid[])) AS post_id
),
viewer_profile AS (
    SELECT p.id AS profile_id
    FROM profiles p
    WHERE p.user_id = :user_id
    LIMIT 1
)
SELECT
    rp.post_id,
    pr.reaction_type,
    (b.id IS NOT NULL) AS is_bookmarked,
    (r.id IS NOT NULL) AS is_reposted
FROM requested_posts rp
LEFT JOIN post_reactions pr
    ON pr.post_id = rp.post_id
   AND pr.user_id = :user_id
LEFT JOIN bookmarks b
    ON b.post_id = rp.post_id
   AND b.user_id = :user_id
LEFT JOIN viewer_profile vp
    ON TRUE
LEFT JOIN reposts r
    ON r.post_id = rp.post_id
   AND r.profile_id = vp.profile_id
   AND r.is_deleted = FALSE
"""


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


def _coerce_reaction_type(value: object | None) -> ReactionType | None:
    if value is None:
        return None
    if isinstance(value, ReactionType):
        return value
    return ReactionType(str(value))


async def fetch_post_engagement_flags(
    db: AsyncSession,
    user_id: UUID,
    post_ids: list[UUID],
) -> PostEngagementFlags:
    if not post_ids:
        return PostEngagementFlags.empty()

    stmt = text(_FETCH_POST_ENGAGEMENT_FLAGS_SQL).bindparams(
        bindparam("post_ids", type_=ARRAY(PGUUID(as_uuid=True))),
        bindparam("user_id", type_=PGUUID(as_uuid=True)),
    )
    rows = (
        await db.execute(
            stmt,
            {
                "post_ids": post_ids,
                "user_id": user_id,
            },
        )
    ).all()

    user_reactions: list[tuple[UUID, ReactionType]] = []
    bookmarked_post_ids: set[UUID] = set()
    reposted_post_ids: set[UUID] = set()
    for post_id, reaction_type, is_bookmarked, is_reposted in rows:
        coerced = _coerce_reaction_type(reaction_type)
        if coerced is not None:
            user_reactions.append((post_id, coerced))
        if is_bookmarked:
            bookmarked_post_ids.add(post_id)
        if is_reposted:
            reposted_post_ids.add(post_id)

    return PostEngagementFlags(
        user_reactions=tuple(user_reactions),
        reposted_post_ids=frozenset(reposted_post_ids),
        bookmarked_post_ids=frozenset(bookmarked_post_ids),
    )
