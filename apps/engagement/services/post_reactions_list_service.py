from __future__ import annotations

from math import ceil
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.post_reaction_list_repository import (
    count_post_reactions,
    fetch_post_reactors,
    fetch_reaction_summary_counts,
    post_exists,
)
from apps.engagement.repositories.share_repository import get_post_share_count
from apps.engagement.schemas import (
    PostReactionsListData,
    PostReactionsListResponse,
    PostReactorItem,
    ReactionSummaryItem,
)
from apps.engagement.services.author_service import format_engagement_author
from apps.engagement.services.reaction_service import format_user_reaction
from apps.engagement.db_models import PostReaction
from common.enums import ReactionType
from common.responses import success_response


def _build_summary(counts: dict[ReactionType, int]) -> list[ReactionSummaryItem]:
    total = sum(counts.values())
    summary = [ReactionSummaryItem(reaction_type="ALL", count=total)]
    for reaction_type in ReactionType:
        summary.append(
            ReactionSummaryItem(
                reaction_type=format_user_reaction(reaction_type),
                count=counts.get(reaction_type, 0),
            )
        )
    return summary


def _format_reactor(
    reaction: PostReaction,
    profile,
    university,
) -> PostReactorItem:
    return PostReactorItem(
        author=format_engagement_author(profile, university),
        reaction_type=format_user_reaction(reaction.reaction_type),
        reacted_at=reaction.created_at,
    )


async def get_post_reactions(
    db: AsyncSession,
    post_id: UUID,
    *,
    reaction_type: ReactionType | None = None,
    page: int = 1,
    limit: int = 20,
) -> PostReactionsListResponse:
    if not await post_exists(db, post_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    summary_counts = await fetch_reaction_summary_counts(db, post_id)
    share_count = await get_post_share_count(db, post_id)
    total = await count_post_reactions(db, post_id, reaction_type)
    offset = (page - 1) * limit
    rows = await fetch_post_reactors(
        db,
        post_id,
        reaction_type=reaction_type,
        offset=offset,
        limit=limit,
    )

    reactors = [
        _format_reactor(reaction, profile, university)
        for reaction, profile, university in rows
    ]
    pages = ceil(total / limit) if total else 0

    return success_response(
        "Reactions fetched successfully",
        PostReactionsListData(
            reactors=reactors,
            summary=_build_summary(summary_counts),
            share_count=share_count,
            total=total,
            page=page,
            limit=limit,
            pages=pages,
        ),
        response_cls=PostReactionsListResponse,
    )
