from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import User
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendation.services.semantic_scholar_service import build_search_query, search_papers
from common.schemas import ApiResponse
from core.database.session import get_session
from core.security.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Recommendation"])


@router.get("/recommendations/papers", response_model=ApiResponse)
async def search_recommendation_papers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """
    Search Semantic Scholar papers using the authenticated user's profile keywords.

    Reads ``profiles.extracted_keywords``, builds a ranked search query, and
    returns the raw Semantic Scholar bulk search response.
    """
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == current_user.id))
    ).scalar_one_or_none()

    extracted_keywords = profile.extracted_keywords if profile else None
    normalized_query = build_search_query(extracted_keywords)
    if not normalized_query:
        print("[semantic-scholar]")
        print(f"user_id={current_user.id}")
        print("query=")
        print("")
        print("papers_found=0")
        return ApiResponse(
            status=False,
            message="No profile keywords available for recommendations",
            data={"data": [], "total": 0},
        )

    result, status_code = await search_papers(normalized_query)

    raw_papers = result.get("data") if isinstance(result, dict) else None
    papers_found = len(raw_papers) if isinstance(raw_papers, list) else 0

    print("[semantic-scholar]")
    print(f"user_id={current_user.id}")
    print("query=")
    print(normalized_query)
    print(f"status_code={status_code}")
    print(f"papers_found={papers_found}")

    logger.info(
        "[semantic-scholar]\nuser_id=%s\nquery=\"%s\"\nstatus_code=%s\npapers_found=%s",
        current_user.id,
        normalized_query,
        status_code,
        papers_found,
    )

    message = "Papers fetched successfully" if papers_found else "No papers found"
    return ApiResponse(message=message, data=result)
