from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from apps.accounts.db_models import User
from apps.recommendation.services.semantic_scholar_service import search_papers
from common.schemas import ApiResponse
from core.security.auth import get_current_user

router = APIRouter(tags=["Recommendation"])


@router.get("/recommendations/papers", response_model=ApiResponse)
async def search_profile_papers(
    query: Annotated[str, Query(..., min_length=1, description="Semantic Scholar search query")],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ApiResponse:
    """
    Search Semantic Scholar papers using a manual query string.

    Returns the raw Semantic Scholar bulk search response.
    """
    normalized_query = query.strip()
    result = await search_papers(normalized_query)

    raw_papers = result.get("data") if isinstance(result, dict) else None
    papers_found = len(raw_papers) if isinstance(raw_papers, list) else 0

    print("[semantic-scholar]")
    print(f"user_id={current_user.id}")
    print("query=")
    print(normalized_query)
    print(f"papers_found={papers_found}")

    message = "Papers fetched successfully" if papers_found else "No papers found"
    return ApiResponse(message=message, data=result)
