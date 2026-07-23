from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from apps.accounts.db_models import User
from apps.recommendation.services import SemanticScholarAPIError, search_papers
from common.schemas import ApiResponse
from core.security.auth import get_current_admin

router = APIRouter(tags=["Recommendation"])


@router.get("/admin/ai-scholar/test", response_model=ApiResponse)
async def test_semantic_scholar_search(
    query: str = Query(..., min_length=1, description="Search term for Semantic Scholar"),
    limit: int = Query(default=10, ge=1, le=100, description="Maximum number of papers to return"),
    _current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    try:
        data = await search_papers(query=query, limit=limit)
    except SemanticScholarAPIError as exc:
        return ApiResponse(status=False, message=exc.message, data=None)

    message = "Papers fetched successfully" if data.papers else "No papers found"
    return ApiResponse(message=message, data=data.model_dump())
