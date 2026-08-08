"""Compact smoke coverage for recommendation helpers and API wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from apps.recommendations.services.engagement_keyword_service import (
    ENGAGEMENT_WEIGHTS,
    update_engagement_keywords,
)
from apps.recommendations.services.keyword_postprocessing import filter_by_score
from apps.recommendations.services.recommendation_query_builder import (
    build_semantic_scholar_query,
)
from apps.recommendations.services.semantic_scholar_service import search_papers


def test_engagement_keyword_update_and_weights() -> None:
    assert ENGAGEMENT_WEIGHTS["like"] == 1
    updated = update_engagement_keywords({"python": 2}, ["Machine Learning", "python"], increment=1)
    assert updated == {"python": 3, "machine learning": 1}


def test_filter_by_score_and_query_builder() -> None:
    filtered = filter_by_score([("rag", 0.9), ("hello", 0.01)], min_score=0.2)
    assert filtered == [("rag", 0.9)]

    query = build_semantic_scholar_query(
        {"major": ["AI"], "content_keywords": {"transformer": 3}, "hashtags": {"nlp": 1}}
    )
    assert isinstance(query, str)
    assert "AI" in query or "transformer" in query or "nlp" in query


@pytest.mark.asyncio
async def test_search_papers_returns_empty_when_request_fails(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(
        "apps.recommendations.services.semantic_scholar_service.settings.semantic_scholar_api_key",
        "test-key",
    )
    with patch(
        "apps.recommendations.services.semantic_scholar_service.httpx.AsyncClient"
    ) as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.get = AsyncMock(side_effect=httpx.ConnectError("offline"))
        client_cls.return_value = client
        result, status = await search_papers("ai", limit=5)

    assert result == {"data": [], "total": 0}
    assert status is None


@pytest.mark.asyncio
async def test_search_papers_trims_bulk_response_to_limit(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.recommendations.services.semantic_scholar_service.settings.semantic_scholar_api_key",
        "test-key",
    )

    oversized = {
        "total": 131,
        "data": [{"paperId": str(i)} for i in range(131)],
        "token": "next-page",
    }

    class _FakeResponse:
        status_code = 200

        def json(self):
            return oversized

    with patch(
        "apps.recommendations.services.semantic_scholar_service.httpx.AsyncClient"
    ) as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.get = AsyncMock(return_value=_FakeResponse())
        client_cls.return_value = client
        result, status = await search_papers("ai", limit=10)

    assert status == 200
    assert len(result["data"]) == 10
    assert result["data"][0]["paperId"] == "0"
    assert result["data"][-1]["paperId"] == "9"
    assert result["total"] == 131


@pytest.mark.asyncio
async def test_recommendation_routes_importable() -> None:
    from apps.recommendations import routes as recommendation_routes

    assert recommendation_routes.router is not None
