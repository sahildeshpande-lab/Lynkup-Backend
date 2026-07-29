from __future__ import annotations

import logging
from typing import Any

import httpx

from apps.recommendation.config import settings
from apps.recommendation.services.keyword_scoring import coerce_keyword_scores, get_top_keywords

logger = logging.getLogger(__name__)

PAPER_SEARCH_BULK_PATH = "/paper/search/bulk"
PAPER_SEARCH_FIELDS = (
    "paperId,title,url,authors,venue,publicationTypes,publicationDate,citationCount,openAccessPdf"
)
DEFAULT_SEARCH_LIMIT = 20
DEFAULT_YEAR_FILTER = "2023-"
REQUEST_TIMEOUT_SECONDS = 30.0

_EMPTY_RESPONSE: dict[str, Any] = {"data": [], "total": 0}


class SemanticScholarAPIError(Exception):
    """Raised when Semantic Scholar returns an unrecoverable client error."""

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


def _normalize_for_dedup(term: str) -> str:
    return " ".join((term or "").strip().lower().split())


def _format_search_term(term: str) -> str:
    return " ".join(word.capitalize() for word in term.strip().split())


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if item and str(item).strip()]
    return []


def build_search_query(extracted_keywords: dict[str, Any] | None) -> str:
    """
    Build a single Semantic Scholar search query from profile keyword data.

    Terms are collected in priority order: major, minor, interests, then the
    top-scoring engagement keywords, hashtags, and content keywords. Duplicates
    are removed case-insensitively.
    """
    if not extracted_keywords:
        return ""

    seen: set[str] = set()
    terms: list[str] = []

    def add_terms(raw_values: list[str]) -> None:
        for value in raw_values:
            normalized = _normalize_for_dedup(value)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            terms.append(_format_search_term(value))

    add_terms(_as_string_list(extracted_keywords.get("major")))
    add_terms(_as_string_list(extracted_keywords.get("minor")))
    add_terms(_as_string_list(extracted_keywords.get("interests")))

    engagement_keywords = coerce_keyword_scores(extracted_keywords.get("engagement_keywords"))
    add_terms(get_top_keywords(engagement_keywords, top_n=3))

    hashtags = coerce_keyword_scores(extracted_keywords.get("hashtags"))
    add_terms(get_top_keywords(hashtags, top_n=3))

    content_keywords = coerce_keyword_scores(extracted_keywords.get("content_keywords"))
    add_terms(get_top_keywords(content_keywords, top_n=3))

    return " ".join(terms)


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


async def search_papers(
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    year: str = DEFAULT_YEAR_FILTER,
) -> dict[str, Any]:
    """
    Search Semantic Scholar papers using the bulk search endpoint.

    Returns the raw JSON response on success. On empty query, API failure, or
    invalid JSON, returns ``{"data": [], "total": 0}`` after logging.
    """
    normalized_query = query.strip()
    if not normalized_query:
        return dict(_EMPTY_RESPONSE)

    params = {
        "query": normalized_query,
        "fields": PAPER_SEARCH_FIELDS,
        "year": year,
        "limit": limit,
    }
    url = f"{settings.semantic_scholar_base_url.rstrip('/')}{PAPER_SEARCH_BULK_PATH}"
    headers = _build_headers()

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException:
        logger.warning(
            "Semantic Scholar API request timed out query=%r api_key_configured=%s",
            normalized_query,
            _api_key_is_configured(),
        )
        return dict(_EMPTY_RESPONSE)
    except httpx.RequestError as exc:
        logger.warning(
            "Semantic Scholar API request failed query=%r api_key_configured=%s error=%s",
            normalized_query,
            _api_key_is_configured(),
            exc,
        )
        return dict(_EMPTY_RESPONSE)

    if response.status_code >= 400:
        error_message = _extract_error_message(response)
        logger.warning(
            "Semantic Scholar API error query=%r status=%s api_key_configured=%s message=%s",
            normalized_query,
            response.status_code,
            _api_key_is_configured(),
            error_message,
        )
        return dict(_EMPTY_RESPONSE)

    try:
        payload = response.json()
    except ValueError:
        logger.warning(
            "Semantic Scholar API returned invalid JSON query=%r",
            normalized_query,
        )
        return dict(_EMPTY_RESPONSE)

    if not isinstance(payload, dict):
        logger.warning(
            "Semantic Scholar API returned unexpected payload type query=%r",
            normalized_query,
        )
        return dict(_EMPTY_RESPONSE)

    return payload
