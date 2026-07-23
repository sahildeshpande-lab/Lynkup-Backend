from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from apps.recommendation.services.semantic_scholar_service import (
    SemanticScholarAPIError,
    _api_key_is_configured,
    _build_headers,
    search_papers,
)


def test_build_headers_includes_api_key_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.recommendation.services.semantic_scholar_service.settings.semantic_scholar_api_key",
        "  test-key  ",
    )

    headers = _build_headers()

    assert headers["x-api-key"] == "test-key"
    assert _api_key_is_configured() is True


def test_build_headers_omits_api_key_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.recommendation.services.semantic_scholar_service.settings.semantic_scholar_api_key",
        "   ",
    )

    headers = _build_headers()

    assert "x-api-key" not in headers
    assert _api_key_is_configured() is False


@pytest.mark.asyncio
async def test_search_papers_success() -> None:
    mock_response = httpx.Response(
        200,
        json={
            "total": 1,
            "offset": 0,
            "next": 1,
            "data": [
                {
                    "paperId": "abc123",
                    "title": "Test Paper",
                    "authors": [{"authorId": "1", "name": "Jane Doe"}],
                    "year": 2024,
                    "abstract": "An abstract",
                    "url": "https://example.com/paper",
                    "citationCount": 10,
                }
            ],
        },
        request=httpx.Request("GET", "https://api.semanticscholar.org/graph/v1/paper/search"),
    )

    with patch(
        "apps.recommendation.services.semantic_scholar_service.httpx.AsyncClient.get",
        new=AsyncMock(return_value=mock_response),
    ):
        result = await search_papers("machine learning", limit=10)

    assert result.total == 1
    assert result.offset == 0
    assert result.next == 1
    assert len(result.papers) == 1
    assert result.papers[0].paperId == "abc123"
    assert result.papers[0].title == "Test Paper"
    assert result.papers[0].authors[0].name == "Jane Doe"
    assert result.papers[0].citationCount == 10


@pytest.mark.asyncio
async def test_search_papers_empty_query_raises() -> None:
    with pytest.raises(SemanticScholarAPIError, match="Search query is required"):
        await search_papers("   ")


@pytest.mark.asyncio
async def test_search_papers_api_error_status() -> None:
    mock_response = httpx.Response(
        429,
        json={"message": "Rate limit exceeded"},
        request=httpx.Request("GET", "https://api.semanticscholar.org/graph/v1/paper/search"),
    )

    with patch(
        "apps.recommendation.services.semantic_scholar_service.httpx.AsyncClient.get",
        new=AsyncMock(return_value=mock_response),
    ):
        with pytest.raises(SemanticScholarAPIError, match="Rate limit exceeded"):
            await search_papers("covid")


@pytest.mark.asyncio
async def test_search_papers_request_error() -> None:
    with patch(
        "apps.recommendation.services.semantic_scholar_service.httpx.AsyncClient.get",
        new=AsyncMock(side_effect=httpx.RequestError("connection failed")),
    ):
        with pytest.raises(SemanticScholarAPIError, match="Unable to reach Semantic Scholar API"):
            await search_papers("covid")
