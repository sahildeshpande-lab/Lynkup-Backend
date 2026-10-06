from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.share.services.branch_service import (
    BranchLinkError,
    create_branch_link,
    extract_branch_code,
)


def test_extract_branch_code_from_short_url() -> None:
    url = "https://jlrh8.test-app.link/tPcFcGY2r6b"
    assert extract_branch_code(url) == "tPcFcGY2r6b"


def test_extract_branch_code_strips_trailing_slash() -> None:
    url = "https://jlrh8.test-app.link/tPcFcGY2r6b/"
    assert extract_branch_code(url) == "tPcFcGY2r6b"


def test_extract_branch_code_rejects_empty_path() -> None:
    with pytest.raises(BranchLinkError, match="did not contain"):
        extract_branch_code("https://jlrh8.test-app.link/")


@pytest.mark.asyncio
async def test_create_branch_link_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.services.branch_service.settings",
        MagicMock(
            branch_key="key_live_test",
            branch_api_url="https://api2.branch.io/v1/url",
        ),
    )
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"url": "https://jlrh8.test-app.link/tPcFcGY2r6b"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("apps.share.services.branch_service.httpx.AsyncClient", return_value=mock_client):
        result = await create_branch_link({"type": "invite"})

    assert result.url == "https://jlrh8.test-app.link/tPcFcGY2r6b"
    assert result.code == "tPcFcGY2r6b"
    mock_client.post.assert_awaited_once()
    payload = mock_client.post.await_args.kwargs["json"]
    assert payload["branch_key"] == "key_live_test"
    assert payload["data"] == {"type": "invite"}
    assert "alias" not in payload


@pytest.mark.asyncio
async def test_create_branch_link_includes_alias_when_provided(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.services.branch_service.settings",
        MagicMock(
            branch_key="key_live_test",
            branch_api_url="https://api2.branch.io/v1/url",
        ),
    )
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"url": "https://jlrh8.test-app.link/share-alias"}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("apps.share.services.branch_service.httpx.AsyncClient", return_value=mock_client):
        result = await create_branch_link({"type": "share"}, alias="share-alias")

    assert result.code == "share-alias"
    payload = mock_client.post.await_args.kwargs["json"]
    assert payload["alias"] == "share-alias"


@pytest.mark.asyncio
async def test_create_branch_link_requires_configured_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.services.branch_service.settings",
        MagicMock(branch_key="  ", branch_api_url="https://api2.branch.io/v1/url"),
    )
    with pytest.raises(BranchLinkError, match="Failed to create Branch link"):
        await create_branch_link({"type": "invite"})


@pytest.mark.asyncio
async def test_create_branch_link_http_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.services.branch_service.settings",
        MagicMock(branch_key="key", branch_api_url="https://api2.branch.io/v1/url"),
    )
    response = MagicMock()
    response.status_code = 500
    response.text = "boom"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("apps.share.services.branch_service.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(BranchLinkError):
            await create_branch_link({"type": "invite"})


@pytest.mark.asyncio
async def test_create_branch_link_timeout(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.services.branch_service.settings",
        MagicMock(branch_key="key", branch_api_url="https://api2.branch.io/v1/url"),
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("apps.share.services.branch_service.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(BranchLinkError):
            await create_branch_link({"type": "invite"})


@pytest.mark.asyncio
async def test_create_branch_link_request_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.services.branch_service.settings",
        MagicMock(branch_key="key", branch_api_url="https://api2.branch.io/v1/url"),
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("offline"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("apps.share.services.branch_service.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(BranchLinkError):
            await create_branch_link({"type": "invite"})


@pytest.mark.asyncio
async def test_create_branch_link_invalid_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.services.branch_service.settings",
        MagicMock(branch_key="key", branch_api_url="https://api2.branch.io/v1/url"),
    )
    response = MagicMock()
    response.status_code = 200
    response.json.side_effect = ValueError("not json")

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("apps.share.services.branch_service.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(BranchLinkError):
            await create_branch_link({"type": "invite"})


@pytest.mark.asyncio
async def test_create_branch_link_missing_url(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.services.branch_service.settings",
        MagicMock(branch_key="key", branch_api_url="https://api2.branch.io/v1/url"),
    )
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {}

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("apps.share.services.branch_service.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(BranchLinkError):
            await create_branch_link({"type": "invite"})
