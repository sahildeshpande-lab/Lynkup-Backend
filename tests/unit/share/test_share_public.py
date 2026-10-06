from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from apps.share.dependencies import require_share_post_token
from apps.share.router import share_post
from apps.share.schemas import SharePostData, SharePostRequest
from apps.share.service import get_shareable_post
from common.enums import UserStatus
from common.exceptions import ApiError
from core.database.session import get_session
from entrypoints.api import app

SHARE_CODE = "tPcFcGY2r6b"


def test_require_share_token_missing() -> None:
    with pytest.raises(ApiError, match="Missing access token"):
        require_share_post_token(None)


def test_require_share_token_blank_expected(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.dependencies.settings",
        SimpleNamespace(share_post_token=""),
    )
    with pytest.raises(ApiError, match="Invalid access token"):
        require_share_post_token("provided")


def test_require_share_token_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.dependencies.settings",
        SimpleNamespace(share_post_token="expected-token"),
    )
    with pytest.raises(ApiError, match="Invalid access token"):
        require_share_post_token("wrong")


def test_require_share_token_compare_digest_type_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.dependencies.settings",
        SimpleNamespace(share_post_token="ok"),
    )
    monkeypatch.setattr(
        "apps.share.dependencies.secrets.compare_digest",
        MagicMock(side_effect=TypeError("bad")),
    )
    with pytest.raises(ApiError, match="Invalid access token"):
        require_share_post_token("ok")


def test_require_share_token_ok(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.share.dependencies.settings",
        SimpleNamespace(share_post_token="share-secret"),
    )
    assert require_share_post_token("share-secret") is None


def _row(*, status=UserStatus.active, is_deleted=False, deleted_at=None, visibility="public"):
    post_id = uuid4()
    author_id = uuid4()
    post = SimpleNamespace(
        id=post_id,
        author_user_id=author_id,
        content={"visibility": visibility, "caption": "hi"},
    )
    author = SimpleNamespace(
        id=author_id,
        status=status,
        is_deleted=is_deleted,
        deleted_at=deleted_at,
    )
    profile = SimpleNamespace(user_id=author_id)
    return post, author, profile


def test_share_post_request_uses_code() -> None:
    payload = SharePostRequest(code=f"  {SHARE_CODE}  ")
    assert payload.code == SHARE_CODE
    with pytest.raises(ValidationError):
        SharePostRequest(code="   ")


@pytest.mark.asyncio
async def test_get_shareable_post_blank_code() -> None:
    db = MagicMock()
    with pytest.raises(ApiError, match="Post not found"):
        await get_shareable_post(db, "   ")
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_get_shareable_post_not_found() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: None))
    with pytest.raises(ApiError, match="Post not found"):
        await get_shareable_post(db, SHARE_CODE)


@pytest.mark.asyncio
async def test_get_shareable_post_hidden_author() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(one_or_none=lambda: _row(status=UserStatus.banned))
    )
    with pytest.raises(ApiError, match="Post not found"):
        await get_shareable_post(db, SHARE_CODE)


@pytest.mark.asyncio
async def test_get_shareable_post_non_public_visibility() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(one_or_none=lambda: _row(visibility="private"))
    )
    with pytest.raises(ApiError, match="Post not found"):
        await get_shareable_post(db, SHARE_CODE)


@pytest.mark.asyncio
async def test_get_shareable_post_success() -> None:
    post, author, profile = _row()
    created = datetime.now(timezone.utc)
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: (post, author, profile)))
    detail = {
        "id": post.id,
        "author_user_id": author.id,
        "author_name": "Ada",
        "profilePhoto_url": "https://cdn.example/p.png",
        "profile_visibility": "public",
        "content": {"caption": "hi", "content_html": "<p>hi</p>", "visibility": "public"},
        "media": [],
        "created_at": created,
    }
    with patch("apps.share.service.format_post_detail", return_value=detail):
        data = await get_shareable_post(db, SHARE_CODE)
    assert data.post_id == post.id
    assert data.author_id == author.id
    assert data.author_name == "Ada"
    assert data.content.caption == "hi"
    assert data.profilePhoto_url == "https://cdn.example/p.png"
    assert data.profile_visibility == "public"
    assert data.content.visibility == "public"
    assert data.media == []
    assert data.created_at == created


@pytest.mark.asyncio
async def test_get_shareable_post_filters_by_branch_code() -> None:
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: None))
    with pytest.raises(ApiError, match="Post not found"):
        await get_shareable_post(db, SHARE_CODE)
    stmt = db.execute.await_args.args[0]
    compiled = stmt.compile()
    sql = str(compiled).lower()
    assert "share_events" in sql or "shareevent" in sql
    assert SHARE_CODE in compiled.params.values()


@pytest.mark.asyncio
async def test_share_post_route_returns_success() -> None:
    post_id = uuid4()
    payload = SharePostRequest(code=SHARE_CODE)
    share_data = SharePostData(
        post_id=post_id,
        author_id=uuid4(),
        author_name="Ada",
        profilePhoto_url="https://cdn.example/p.png",
        profile_visibility="public",
        content={
            "caption": "hi",
            "content_html": "<p>hi</p>",
            "visibility": "public",
        },
        media=[],
        created_at=datetime.now(timezone.utc),
    )
    db = MagicMock()
    with patch(
        "apps.share.router.get_shareable_post",
        new=AsyncMock(return_value=share_data),
    ) as get_post:
        response = await share_post(payload, db)
    get_post.assert_awaited_once_with(db, SHARE_CODE)
    assert response.status is True
    assert response.message == "Post retrieved successfully"
    assert response.data == share_data


def test_share_post_http_requires_share_token() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.post("/api/v1/share/post", json={"code": SHARE_CODE})
    assert response.status_code == 401
    body = response.json()
    assert body["status"] is False
    assert body["message"] == "Missing access token"


def test_share_post_http_rejects_post_id_body(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setattr(
        "apps.share.dependencies.settings",
        SimpleNamespace(share_post_token="share-secret"),
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/share/post",
        json={"post_id": str(uuid4())},
        headers={"X-Share-Token": "share-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert "code" in body["message"].lower()


def test_share_post_http_share_token_returns_expected_payload(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    post_id = uuid4()
    author_id = uuid4()
    created = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    share_data = SharePostData(
        post_id=post_id,
        author_id=author_id,
        author_name="Ada Lovelace",
        profilePhoto_url="https://cdn.example/p.png",
        profile_visibility="public",
        content={
            "caption": "Campus update",
            "content_html": "<p>Campus update</p>",
            "visibility": "public",
        },
        media=[],
        created_at=created,
    )

    async def _override_session():
        yield MagicMock()

    monkeypatch.setattr(
        "apps.share.dependencies.settings",
        SimpleNamespace(share_post_token="share-secret"),
    )
    monkeypatch.setattr(
        "apps.share.router.get_shareable_post",
        AsyncMock(return_value=share_data),
    )
    app.dependency_overrides[get_session] = _override_session
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/share/post",
            json={"code": SHARE_CODE},
            headers={"X-Share-Token": "share-secret"},
        )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Post retrieved successfully"
    assert body["data"]["post_id"] == str(post_id)
    assert body["data"]["author_id"] == str(author_id)
    assert body["data"]["author_name"] == "Ada Lovelace"
    assert body["data"]["profilePhoto_url"] == "https://cdn.example/p.png"
    assert body["data"]["profile_visibility"] == "public"
    assert body["data"]["content"] == {
        "caption": "Campus update",
        "content_html": "<p>Campus update</p>",
        "visibility": "public",
    }
    assert body["data"]["media"] == []
    assert "created_at" in body["data"]
