"""Learning Spotlight – V2 Semantic Scholar adapter.

Thin async wrapper around the same Semantic Scholar ``/paper/search/bulk``
endpoint used by V1.  Differences from V1:

* Requests **extended fields** that V2 strategies need (``abstract``,
  ``fieldsOfStudy``, ``s2FieldsOfStudy``).
* Accepts an optional custom ``fields`` override per call.
* Enforces a global request-rate limit (Redis when available).
* Distinguishes genuine empty results from rate-limit / upstream failures.
* Does **not** modify V1 code — reuses the same config/credentials.

Usage::

    from apps.learningspotlight.services.semantic_scholar_adapter import (
        search_papers_v2,
    )

    payload, status = await search_papers_v2("machine learning", limit=20)
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from apps.learningspotlight.config import settings as spotlight_settings
from apps.learningspotlight.services.semantic_scholar_rate_limiter import (
    acquire_semantic_scholar_permit,
)
from apps.recommendations.config import settings as rec_settings

logger = logging.getLogger(__name__)

# Extended field set for V2 — includes abstract + fieldsOfStudy which V1 omits.
V2_PAPER_SEARCH_FIELDS = (
    "paperId,title,url,authors,venue,"
    "publicationTypes,publicationDate,citationCount,openAccessPdf,"
    "abstract,fieldsOfStudy,s2FieldsOfStudy,year"
)

_PAPER_SEARCH_BULK_PATH = "/paper/search/bulk"
_REQUEST_TIMEOUT_SECONDS = 30.0
_MAX_SEARCH_ATTEMPTS = 3
_MAX_RETRY_AFTER_SECONDS = 60.0

_EMPTY_RESPONSE: dict[str, Any] = {"data": [], "total": 0}
_TOO_MANY_HITS_MARKER = "too many hits"


class SemanticScholarExternalError(Exception):
    """Retryable Semantic Scholar failure (rate limit or upstream 5xx).

    Distinct from a genuine empty search result (``200`` with ``data=[]``).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        retryable: bool = True,
    ) -> None:
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


_PLACEHOLDER_FIELDS_OF_STUDY = frozenset(
    {
        "na",
        "n/a",
        "n.a",
        "n.a.",
        "n a",
        "none",
        "null",
        "nil",
        "undefined",
        "-",
        "--",
        "not applicable",
        "not available",
    }
)


def is_placeholder_fields_of_study_label(value: str) -> bool:
    """True for empty / sentinel labels that must not be sent as fieldsOfStudy."""
    return value.casefold() in _PLACEHOLDER_FIELDS_OF_STUDY


def _join_fields_of_study(values: list[str] | None) -> str | None:
    """Comma-join caller-supplied profile labels; do not map or allowlist them."""
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in values or []:
        item = " ".join(str(raw or "").split())
        if not item or is_placeholder_fields_of_study_label(item):
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return ",".join(cleaned) if cleaned else None


def is_too_many_hits_error(payload: dict[str, Any] | None, status: int | None) -> bool:
    """True when Semantic Scholar rejected a query as matching too much of the corpus."""
    if status != 400:
        return False
    blob = " ".join(
        str((payload or {}).get(key) or "")
        for key in ("error", "message")
    ).lower()
    return _TOO_MANY_HITS_MARKER in blob


def _build_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    api_key = rec_settings.semantic_scholar_api_key
    if api_key and api_key.strip():
        headers["x-api-key"] = api_key.strip()
    return headers


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return min(max(0.0, float(raw)), _MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    # Exponential backoff with jitter when Retry-After is absent.
    base = float(2 ** attempt)
    jitter = random.uniform(0.0, 0.5)
    return min(base + jitter, _MAX_RETRY_AFTER_SECONDS)


def _parse_search_payload(
    response: httpx.Response,
    *,
    cap: int,
) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        logger.warning(
            "V2 SS adapter: invalid JSON  status=%s",
            response.status_code,
        )
        return None

    if not isinstance(payload, dict):
        return None

    data = payload.get("data")
    if isinstance(data, list) and len(data) > cap:
        return {**payload, "data": data[:cap]}
    return payload


async def search_papers_v2(
    query: str,
    *,
    limit: int | None = None,
    year: str | None = None,
    fields: str | None = None,
    fields_of_study: list[str] | None = None,
) -> tuple[dict[str, Any], int | None]:
    """Search Semantic Scholar papers with V2-extended fields.

    On success returns ``(payload, http_status)``.  A genuine empty result is
    ``status=200`` with ``data=[]``.

    After bounded retries, HTTP ``429`` and ``5xx`` raise
    ``SemanticScholarExternalError`` — they are **never** reported as a normal
    zero-result search.

    Transient timeouts / network errors still retry; on exhaustion they return
    an empty payload with ``status=None`` (unchanged contract for transport
    failures).
    """
    normalized_query = query.strip()
    if not normalized_query:
        return dict(_EMPTY_RESPONSE), None

    effective_limit = (
        limit
        if limit is not None
        else spotlight_settings.learning_spotlight_candidate_limit
    )
    requested_fields = fields or V2_PAPER_SEARCH_FIELDS
    fields_of_study_param = _join_fields_of_study(fields_of_study)

    params: dict[str, Any] = {
        "query": normalized_query,
        "fields": requested_fields,
        "limit": effective_limit,
    }
    if year:
        params["year"] = year
    if fields_of_study_param:
        params["fieldsOfStudy"] = fields_of_study_param

    base_url = rec_settings.semantic_scholar_base_url.rstrip("/")
    url = f"{base_url}{_PAPER_SEARCH_BULK_PATH}"
    headers = _build_headers()

    last_status: int | None = None
    last_retryable_error: str | None = None
    for attempt in range(_MAX_SEARCH_ATTEMPTS):
        await acquire_semantic_scholar_permit()
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException:
            last_status = None
            logger.warning(
                "V2 SS adapter: timeout  query=%r  attempt=%s/%s",
                normalized_query,
                attempt + 1,
                _MAX_SEARCH_ATTEMPTS,
            )
            if attempt < _MAX_SEARCH_ATTEMPTS - 1:
                await asyncio.sleep(2 ** attempt)
            continue
        except httpx.RequestError as exc:
            last_status = None
            logger.warning(
                "V2 SS adapter: request error  query=%r  attempt=%s/%s  error=%s",
                normalized_query,
                attempt + 1,
                _MAX_SEARCH_ATTEMPTS,
                exc,
            )
            if attempt < _MAX_SEARCH_ATTEMPTS - 1:
                await asyncio.sleep(2 ** attempt)
            continue

        if response.status_code == 429:
            last_status = 429
            wait = _retry_after_seconds(response, attempt)
            last_retryable_error = "rate_limited"
            logger.warning(
                "V2 SS adapter: rate limited  query=%r  attempt=%s/%s  "
                "status=429  retry_delay=%.3fs",
                normalized_query,
                attempt + 1,
                _MAX_SEARCH_ATTEMPTS,
                wait,
            )
            if attempt < _MAX_SEARCH_ATTEMPTS - 1:
                await asyncio.sleep(wait)
                continue
            logger.error(
                "V2 SS adapter: rate limit exhausted  query=%r  attempts=%s",
                normalized_query,
                _MAX_SEARCH_ATTEMPTS,
            )
            raise SemanticScholarExternalError(
                "Semantic Scholar rate limit exceeded",
                status_code=429,
                retryable=True,
            )

        if response.status_code >= 500:
            last_status = response.status_code
            wait = _retry_after_seconds(response, attempt)
            last_retryable_error = "upstream_5xx"
            logger.warning(
                "V2 SS adapter: API error  query=%r  status=%s  attempt=%s/%s  "
                "retry_delay=%.3fs  message=%s",
                normalized_query,
                response.status_code,
                attempt + 1,
                _MAX_SEARCH_ATTEMPTS,
                wait,
                response.text[:300],
            )
            if attempt < _MAX_SEARCH_ATTEMPTS - 1:
                await asyncio.sleep(wait)
                continue
            logger.error(
                "V2 SS adapter: upstream failure exhausted  query=%r  status=%s  "
                "attempts=%s",
                normalized_query,
                last_status,
                _MAX_SEARCH_ATTEMPTS,
            )
            raise SemanticScholarExternalError(
                f"Semantic Scholar upstream error status={last_status}",
                status_code=last_status,
                retryable=True,
            )

        if response.status_code >= 400:
            error_text = response.text[:300]
            logger.warning(
                "V2 SS adapter: API error  query=%r  status=%s  message=%s",
                normalized_query,
                response.status_code,
                error_text,
            )
            return {**_EMPTY_RESPONSE, "error": error_text}, response.status_code

        payload = _parse_search_payload(response, cap=effective_limit)
        if payload is None:
            return dict(_EMPTY_RESPONSE), response.status_code
        return payload, response.status_code

    # Transport failures only (timeout / network) reach here.
    logger.warning(
        "V2 SS adapter: transport failure exhausted  query=%r  last_status=%s  "
        "last_error=%s",
        normalized_query,
        last_status,
        last_retryable_error,
    )
    return dict(_EMPTY_RESPONSE), last_status


_AUTHOR_BATCH_PATH = "/author/batch"
_AUTHOR_SEARCH_FIELDS = "authorId,name,citationCount,hIndex,paperCount,affiliations"


async def get_authors_batch_v2(
    author_ids: list[str],
    *,
    fields: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch author impact metrics (citations, h-index, paper count) for a list of author IDs.

    Uses Semantic Scholar ``POST /author/batch`` with graceful error handling.
    Returns a dict mapping author_id -> author metadata dict.
    """
    cleaned_ids = [str(aid).strip() for aid in author_ids if aid and str(aid).strip()]
    if not cleaned_ids:
        return {}

    base_url = rec_settings.semantic_scholar_base_url.rstrip("/")
    url = f"{base_url}{_AUTHOR_BATCH_PATH}"
    headers = _build_headers()
    params = {"fields": fields or _AUTHOR_SEARCH_FIELDS}
    body = {"ids": cleaned_ids}

    await acquire_semantic_scholar_permit()
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, params=params, json=body, headers=headers)
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.warning("V2 SS adapter: author batch request error: %s", exc)
        return {}

    if response.status_code == 429:
        logger.warning("V2 SS adapter: author batch rate limited")
        raise SemanticScholarExternalError(
            "Semantic Scholar author batch rate limit exceeded",
            status_code=429,
            retryable=True,
        )

    if response.status_code != 200:
        logger.warning(
            "V2 SS adapter: author batch returned status %s",
            response.status_code,
        )
        return {}

    try:
        data = response.json()
    except ValueError:
        return {}

    if not isinstance(data, list):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for item in data:
        if isinstance(item, dict) and item.get("authorId"):
            result[str(item["authorId"])] = item

    return result
