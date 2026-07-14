from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.post_reaction_list_repository import (
    count_post_reactions,
    fetch_post_reactors,
    fetch_reaction_summary_counts,
    post_exists,
)
from apps.engagement.schemas import (
    PostReactionsListData,
    PostReactionsListResponse,
    ReactionSummaryItem,
)
from apps.engagement.services.post_reaction_formatters import (
    build_post_reactions_response,
    format_post_reactor_profile,
)
from apps.engagement.services.reaction_service import format_user_reaction
from common.enums import ReactionType
from common.pagination import build_paginated_response
from common.responses import success_response


def _build_summary(counts: dict[ReactionType, int]) -> list[ReactionSummaryItem]:
    return [
        ReactionSummaryItem(
            reaction_type=format_user_reaction(reaction_type),
            count=counts.get(reaction_type, 0),
        )
        for reaction_type in ReactionType
    ]


async def get_post_reactions(
    db: AsyncSession,
    post_id: UUID,
    *,
    reaction_type: ReactionType | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> PostReactionsListResponse:
    if not await post_exists(db, post_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    summary_counts = await fetch_reaction_summary_counts(db, post_id)
    summary = _build_summary(summary_counts)

    if page is not None and page_size is not None:
        total_items = await count_post_reactions(db, post_id, reaction_type)
        rows = await fetch_post_reactors(
            db,
            post_id,
            reaction_type=reaction_type,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        pagination = build_paginated_response([], page, page_size, total_items)
        page_value = pagination.page
        page_size_value = pagination.pageSize
        total_items_value = pagination.totalItems
        total_pages_value = pagination.totalPages
    else:
        rows = await fetch_post_reactors(
            db,
            post_id,
            reaction_type=reaction_type,
            offset=0,
            limit=None,
        )
        total_items_value = len(rows)
        page_value = 1
        page_size_value = total_items_value
        total_pages_value = 1 if rows else 0

    reactors = [
        format_post_reactor_profile(reaction, profile, university)
        for reaction, profile, university in rows
    ]

    return success_response(
        "Reactions fetched successfully",
        PostReactionsListData(
            reactions=build_post_reactions_response(reactors, reaction_type=reaction_type),
            summary=summary,
            page=page_value,
            pageSize=page_size_value,
            totalItems=total_items_value,
            totalPages=total_pages_value,
        ),
        response_cls=PostReactionsListResponse,
    )
