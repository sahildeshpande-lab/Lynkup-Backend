from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from apps.recommendation.services.semantic_scholar_service import (
    _api_key_is_configured,
    _build_headers,
    build_search_query,
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


def test_build_search_query_prioritizes_and_deduplicates_terms() -> None:
    extracted_keywords = {
        "major": ["Artificial Intelligence"],
        "minor": ["Data Science"],
        "interests": [
            "Artificial Intelligence",
            "Deep Learning",
            "Data Science",
        ],
        "hashtags": {
            "machinelearning": 3,
            "ai": 2,
            "nlp": 1,
        },
        "engagement_keywords": {
            "rag": 4,
            "llm": 2,
        },
        "content_keywords": {
            "semantic search": 5,
            "vector database": 3,
            "rag": 1,
        },
    }

    query = build_search_query(extracted_keywords)

    assert query == (
        "Artificial Intelligence Data Science Deep Learning Rag Llm "
        "Semantic Search Vector Database Machinelearning Ai Nlp"
    )


def test_build_search_query_returns_empty_string_for_missing_profile() -> None:
    assert build_search_query(None) == ""
    assert build_search_query({}) == ""


@pytest.mark.asyncio
async def test_search_papers_returns_raw_response() -> None:
    raw_payload = {
        "total": 1,
        "token": "next-token",
        "data": [
            {
                "paperId": "abc123",
                "title": "Test Paper",
                "url": "https://example.com/paper",
                "citationCount": 10,
            }
        ],
    }
    mock_response = httpx.Response(
        200,
        json=raw_payload,
        request=httpx.Request(
            "GET",
            "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
        ),
    )

    with patch(
        "apps.recommendation.services.semantic_scholar_service.httpx.AsyncClient.get",
        new=AsyncMock(return_value=mock_response),
    ) as mock_get:
        result, status_code = await search_papers("Machine Learning")

    assert result == raw_payload
    assert status_code == 200
    mock_get.assert_awaited_once()
    call_kwargs = mock_get.await_args.kwargs
    assert call_kwargs["params"]["query"] == "Machine Learning"
    assert call_kwargs["params"]["limit"] == 10
    assert call_kwargs["params"]["year"] == "2023-"
    assert "paperId" in call_kwargs["params"]["fields"]


@pytest.mark.asyncio
async def test_search_papers_empty_query_returns_empty_data() -> None:
    result, status_code = await search_papers("   ")

    assert result == {"data": [], "total": 0}
    assert status_code is None


@pytest.mark.asyncio
async def test_search_papers_api_error_returns_empty_data() -> None:
    mock_response = httpx.Response(
        429,
        json={"message": "Rate limit exceeded"},
        request=httpx.Request(
            "GET",
            "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
        ),
    )

    with patch(
        "apps.recommendation.services.semantic_scholar_service.httpx.AsyncClient.get",
        new=AsyncMock(return_value=mock_response),
    ):
        result, status_code = await search_papers("covid")

    assert result == {"data": [], "total": 0}
    assert status_code == 429


@pytest.mark.asyncio
async def test_search_papers_request_error_returns_empty_data() -> None:
    with patch(
        "apps.recommendation.services.semantic_scholar_service.httpx.AsyncClient.get",
        new=AsyncMock(side_effect=httpx.RequestError("connection failed")),
    ):
        result, status_code = await search_papers("covid")

    assert result == {"data": [], "total": 0}
    assert status_code is None
