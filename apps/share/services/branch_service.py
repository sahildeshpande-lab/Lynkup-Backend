from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse
from typing import Any

import httpx

from apps.share.config import settings

logger = logging.getLogger(__name__)

BRANCH_REQUEST_TIMEOUT_SECONDS = 10.0


class BranchLinkError(Exception):
    """Raised when Branch.io link creation fails."""


@dataclass(frozen=True)
class BranchLinkResult:
    url: str
    code: str


def extract_branch_code(url: str) -> str:
    """Return the final path segment of a Branch short URL."""
    parsed = urlparse((url or "").strip())
    path = (parsed.path or "").rstrip("/")
    code = path.rsplit("/", 1)[-1] if path else ""
    if not code:
        raise BranchLinkError("Branch URL did not contain a link code")
    return code


def _branch_key_kind(branch_key: str) -> str:
    if branch_key.startswith("key_live"):
        return "live"
    if branch_key.startswith("key_test"):
        return "test"
    return "unknown"


async def create_branch_link(data: dict[str, Any], *, alias: str | None = None) -> BranchLinkResult:
    """Create a Branch.io short URL and extract its generated code.

    Branch is the source of the short-link identifier. The backend never
    invents invite/share codes.
    """
    branch_key = (settings.branch_key or "").strip()
    if not branch_key:
        logger.error("BRANCH_KEY is not configured")
        raise BranchLinkError("Failed to create Branch link")

    payload = {
        "branch_key": branch_key,
        "data": data,
    }
    if alias:
        payload["alias"] = alias
    url = (settings.branch_api_url or "").strip() or "https://api2.branch.io/v1/url"
    logger.info(
        "Branch.io create-link request key_kind=%s api_url=%s data=%s",
        _branch_key_kind(branch_key),
        url,
        data,
    )

    try:
        async with httpx.AsyncClient(timeout=BRANCH_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        logger.warning("Branch.io request timed out data=%s", data)
        raise BranchLinkError("Failed to create Branch link") from exc
    except httpx.RequestError as exc:
        logger.warning("Branch.io request failed error=%s data=%s", exc, data)
        raise BranchLinkError("Failed to create Branch link") from exc

    if response.status_code >= 400:
        logger.warning(
            "Branch.io API error status=%s body=%s data=%s",
            response.status_code,
            response.text[:500],
            data,
        )
        raise BranchLinkError("Failed to create Branch link")

    try:
        body = response.json()
    except ValueError as exc:
        logger.warning(
            "Branch.io returned non-JSON response status=%s body=%s data=%s",
            response.status_code,
            response.text[:500],
            data,
        )
        raise BranchLinkError("Failed to create Branch link") from exc

    branch_url = (body.get("url") or "").strip() if isinstance(body, dict) else ""
    if not branch_url:
        logger.warning(
            "Branch.io response missing url field status=%s body=%s data=%s",
            response.status_code,
            str(body)[:500],
            data,
        )
        raise BranchLinkError("Failed to create Branch link")

    try:
        code = extract_branch_code(branch_url)
    except BranchLinkError:
        logger.warning("Branch.io URL missing link code url=%s data=%s", branch_url, data)
        raise

    logger.info("Branch.io link created url=%s code=%s data=%s", branch_url, code, data)
    return BranchLinkResult(url=branch_url, code=code)
