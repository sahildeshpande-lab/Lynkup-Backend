from __future__ import annotations

import logging

import httpx

from apps.recommendation.config import settings
from apps.recommendation.schemas import (
    SemanticScholarAuthor,
    SemanticScholarPaper,
    SemanticScholarSearchData,
)

logger = logging.getLogger(__name__)

PAPER_SEARCH_FIELDS = "paperId,title,authors,year,abstract,url,citationCount"
REQUEST_TIMEOUT_SECONDS = 30.0


class SemanticScholarAPIError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _api_key_is_configured() -> bool:
    api_key = settings.semantic_scholar_api_key
    return bool(api_key and api_key.strip())


def _build_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    api_key = settings.semantic_scholar_api_key
    if api_key and api_key.strip():
        headers["x-api-key"] = api_key.strip()
    return headers


def _log_request_context(query: str, limit: int) -> None:
    api_key_configured = _api_key_is_configured()
    logger.info(
        "Semantic Scholar paper search: query=%r limit=%s api_key_configured=%s x_api_key_header_sent=%s",
        query,
        limit,
        api_key_configured,
        api_key_configured,
    )
    if not api_key_configured:
        logger.warning(
            "Semantic Scholar requests without SEMANTIC_SCHOLAR_API_KEY use public rate limits "
            "(~100 requests per 5 minutes). Set SEMANTIC_SCHOLAR_API_KEY in .env and restart the server."
        )


def _parse_authors(raw_authors: list[dict] | None) -> list[SemanticScholarAuthor]:
    if not raw_authors:
        return []

    return [
        SemanticScholarAuthor(
            authorId=author.get("authorId"),
            name=author.get("name"),
        )
        for author in raw_authors
        if isinstance(author, dict)
    ]


def _parse_paper(raw_paper: dict) -> SemanticScholarPaper | None:
    paper_id = raw_paper.get("paperId")
    if not paper_id:
        return None

    return SemanticScholarPaper(
        paperId=str(paper_id),
        title=raw_paper.get("title"),
        authors=_parse_authors(raw_paper.get("authors")),
        year=raw_paper.get("year"),
        abstract=raw_paper.get("abstract"),
        url=raw_paper.get("url"),
        citationCount=raw_paper.get("citationCount"),
    )


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"Semantic Scholar API returned status {response.status_code}"

    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return f"Semantic Scholar API returned status {response.status_code}"


async def search_papers(query: str, limit: int = 10) -> SemanticScholarSearchData:
    normalized_query = query.strip()
    if not normalized_query:
        raise SemanticScholarAPIError("Search query is required")

    params = {
        "query": normalized_query,
        "limit": limit,
        "fields": PAPER_SEARCH_FIELDS,
    }
    url = f"{settings.semantic_scholar_base_url.rstrip('/')}/paper/search"
    headers = _build_headers()
    _log_request_context(normalized_query, limit)

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        logger.warning("Semantic Scholar API request timed out for query=%r", normalized_query)
        raise SemanticScholarAPIError("Semantic Scholar API request timed out") from exc
    except httpx.RequestError as exc:
        logger.warning("Semantic Scholar API request failed for query=%r: %s", normalized_query, exc)
        raise SemanticScholarAPIError("Unable to reach Semantic Scholar API") from exc

    if response.status_code >= 400:
        error_message = _extract_error_message(response)
        if response.status_code == 429:
            logger.warning(
                "Semantic Scholar rate limit hit for query=%r: api_key_configured=%s x_api_key_header_sent=%s message=%s",
                normalized_query,
                _api_key_is_configured(),
                "x-api-key" in headers,
                error_message,
            )
        else:
            logger.warning(
                "Semantic Scholar API error for query=%r: status=%s api_key_configured=%s message=%s",
                normalized_query,
                response.status_code,
                _api_key_is_configured(),
                error_message,
            )
        raise SemanticScholarAPIError(error_message)

    logger.info(
        "Semantic Scholar paper search succeeded for query=%r: status=%s api_key_configured=%s",
        normalized_query,
        response.status_code,
        _api_key_is_configured(),
    )

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("Semantic Scholar API returned invalid JSON for query=%r", normalized_query)
        raise SemanticScholarAPIError("Invalid response from Semantic Scholar API") from exc

    raw_papers = payload.get("data") if isinstance(payload, dict) else None
    papers: list[SemanticScholarPaper] = []
    if isinstance(raw_papers, list):
        for raw_paper in raw_papers:
            if isinstance(raw_paper, dict):
                parsed = _parse_paper(raw_paper)
                if parsed is not None:
                    papers.append(parsed)

    return SemanticScholarSearchData(
        total=int(payload.get("total") or 0) if isinstance(payload, dict) else 0,
        offset=int(payload.get("offset") or 0) if isinstance(payload, dict) else 0,
        next=payload.get("next") if isinstance(payload, dict) else None,
        papers=papers,
    )
