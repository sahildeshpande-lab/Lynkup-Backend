"""Unit tests for Learning Spotlight Semantic Scholar adapter retries."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from apps.learningspotlight.services.semantic_scholar_adapter import (
    V2_PAPER_SEARCH_FIELDS,
    SemanticScholarExternalError,
    search_papers_v2,
)


def _response(status_code: int, payload: dict | None = None, retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after else {}
    request = httpx.Request("GET", "https://api.semanticscholar.org/graph/v1/paper/search/bulk")
    if payload is None:
        return httpx.Response(status_code, headers=headers, request=request, text="")
    return httpx.Response(status_code, headers=headers, request=request, json=payload)


class _FakeAsyncClient:
    def __init__(self, responses: list[httpx.Response], **kwargs):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        return self._responses.pop(0)


class _CapturingAsyncClient(_FakeAsyncClient):
    def __init__(self, responses: list[httpx.Response], captured: dict, **kwargs):
        super().__init__(responses, **kwargs)
        self._captured = captured

    async def get(self, url, params=None, headers=None):
        self._captured["url"] = url
        self._captured["params"] = params
        self._captured["headers"] = headers
        return await super().get(url, params=params, headers=headers)


_RATE_LIMIT_PATCH = (
    "apps.learningspotlight.services.semantic_scholar_adapter.acquire_semantic_scholar_permit",
    AsyncMock(return_value=0.0),
)


@pytest.mark.asyncio
async def test_search_papers_v2_sends_fields_of_study_filter_and_return_fields() -> None:
    papers = {
        "data": [{"paperId": "p1", "title": "Related", "abstract": "Abstract text."}],
        "total": 1,
    }
    captured: dict = {}
    responses = [_response(200, papers)]

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _CapturingAsyncClient(responses, captured, **kwargs),
        ),
    ):
        payload, status = await search_papers_v2(
            "history",
            limit=10,
            fields_of_study=["History", "Literature"],
        )

    assert status == 200
    assert payload["data"][0]["paperId"] == "p1"
    params = captured["params"]
    assert params["fieldsOfStudy"] == "History,Literature"
    assert "fieldsOfStudy" in params["fields"]
    assert params["fields"] == V2_PAPER_SEARCH_FIELDS


@pytest.mark.asyncio
async def test_search_papers_v2_omits_fields_of_study_when_none() -> None:
    papers = {
        "data": [{"paperId": "p1", "title": "Related", "abstract": "Abstract text."}],
        "total": 1,
    }
    captured: dict = {}
    responses = [_response(200, papers)]

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _CapturingAsyncClient(responses, captured, **kwargs),
        ),
    ):
        await search_papers_v2("history", limit=10, fields_of_study=None)

    assert "fieldsOfStudy" not in captured["params"]


@pytest.mark.asyncio
async def test_search_papers_v2_drops_placeholder_fields_of_study() -> None:
    papers = {
        "data": [{"paperId": "p1", "title": "Related", "abstract": "Abstract text."}],
        "total": 1,
    }
    captured: dict = {}
    responses = [_response(200, papers)]

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _CapturingAsyncClient(responses, captured, **kwargs),
        ),
    ):
        await search_papers_v2(
            "history",
            limit=10,
            fields_of_study=["N/A", "None", "History", "Na"],
        )

    assert captured["params"]["fieldsOfStudy"] == "History"


@pytest.mark.asyncio
async def test_search_papers_v2_sends_api_key_header(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.learningspotlight.services.semantic_scholar_adapter.rec_settings.semantic_scholar_api_key",
        "test-api-key",
    )
    papers = {"data": [], "total": 0}
    captured: dict = {}
    responses = [_response(200, papers)]

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _CapturingAsyncClient(responses, captured, **kwargs),
        ),
    ):
        payload, status = await search_papers_v2("history", limit=10)

    assert status == 200
    assert payload == {"data": [], "total": 0}
    assert captured["headers"]["x-api-key"] == "test-api-key"


@pytest.mark.asyncio
async def test_search_papers_v2_200_empty_is_genuine_zero_result() -> None:
    responses = [_response(200, {"data": [], "total": 0})]

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _FakeAsyncClient(responses, **kwargs),
        ),
    ):
        payload, status = await search_papers_v2("obscure-topic-xyz", limit=10)

    assert status == 200
    assert payload["data"] == []
    assert payload["total"] == 0


@pytest.mark.asyncio
async def test_search_papers_v2_retries_429_with_retry_after_then_succeeds() -> None:
    papers = {
        "data": [{"paperId": "p1", "title": "Related", "abstract": "Abstract text."}],
        "total": 1,
    }
    responses = [
        _response(429, retry_after="1"),
        _response(200, papers),
    ]
    sleep = AsyncMock()

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _FakeAsyncClient(responses, **kwargs),
        ),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.asyncio.sleep",
            new=sleep,
        ),
    ):
        payload, status = await search_papers_v2("attention", limit=10)

    assert status == 200
    assert payload["data"][0]["paperId"] == "p1"
    sleep.assert_awaited()
    assert sleep.await_args.args[0] == 1.0


@pytest.mark.asyncio
async def test_search_papers_v2_retries_429_without_retry_after() -> None:
    papers = {
        "data": [{"paperId": "p1", "title": "Related", "abstract": "Abstract text."}],
        "total": 1,
    }
    responses = [
        _response(429),
        _response(200, papers),
    ]
    sleep = AsyncMock()

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _FakeAsyncClient(responses, **kwargs),
        ),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.asyncio.sleep",
            new=sleep,
        ),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.random.uniform",
            return_value=0.1,
        ),
    ):
        payload, status = await search_papers_v2("attention", limit=10)

    assert status == 200
    assert payload["data"][0]["paperId"] == "p1"
    # attempt index 0 → 2**0 + 0.1 = 1.1
    assert sleep.await_args.args[0] == pytest.approx(1.1)


@pytest.mark.asyncio
async def test_search_papers_v2_exhausted_429_raises_external_error() -> None:
    responses = [
        _response(429, retry_after="0"),
        _response(429, retry_after="0"),
        _response(429, retry_after="0"),
    ]

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _FakeAsyncClient(responses, **kwargs),
        ),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.asyncio.sleep",
            new=AsyncMock(),
        ),
        pytest.raises(SemanticScholarExternalError) as exc_info,
    ):
        await search_papers_v2("attention", limit=10)

    assert exc_info.value.status_code == 429
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_search_papers_v2_exhausted_5xx_raises_external_error() -> None:
    responses = [
        _response(503),
        _response(503),
        _response(503),
    ]

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _FakeAsyncClient(responses, **kwargs),
        ),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.asyncio.sleep",
            new=AsyncMock(),
        ),
        pytest.raises(SemanticScholarExternalError) as exc_info,
    ):
        await search_papers_v2("attention", limit=10)

    assert exc_info.value.status_code == 503
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_search_papers_v2_too_many_hits_returns_error_payload() -> None:
    from apps.learningspotlight.services.semantic_scholar_adapter import (
        is_too_many_hits_error,
    )

    body = {
        "error": "Search returned too many hits (33457472 of 10000000) Refine or consider using the datasets API to download the full corpus."
    }
    responses = [_response(400, body)]

    with (
        patch(_RATE_LIMIT_PATCH[0], new=_RATE_LIMIT_PATCH[1]),
        patch(
            "apps.learningspotlight.services.semantic_scholar_adapter.httpx.AsyncClient",
            side_effect=lambda **kwargs: _FakeAsyncClient(responses, **kwargs),
        ),
    ):
        payload, status = await search_papers_v2("engineering", limit=10)

    assert status == 400
    assert payload["data"] == []
    assert is_too_many_hits_error(payload, status) is True
    assert is_too_many_hits_error({"data": [], "total": 0}, 200) is False
