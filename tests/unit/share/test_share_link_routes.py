from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.share.schemas import ShareLinkData, ShareLinkResponse, ShareLinkType
from core.database.session import get_session
from core.security.auth import get_current_app_user, get_current_user
from entrypoints.api import app


client = TestClient(app)


class _NoopSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("db session should not be used in this route test")

    def add(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None

    async def refresh(self, *_args, **_kwargs):
        return None

    async def rollback(self):
        return None


def test_share_link_unauthenticated_is_rejected() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_current_app_user, None)
    app.dependency_overrides.pop(get_session, None)
    try:
        response = client.post("/api/v1/share/link", json={"type": "invite"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_app_user, None)

    assert response.status_code == 401
    body = response.json()
    assert body["status"] is False


def test_share_link_invite_route_uses_authenticated_user(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), role="user")

    async def _override_user():
        return user

    async def _override_session():
        yield _NoopSession()

    async def _mock_create_link(db, user_id, payload):
        assert user_id == user.id
        assert payload.type == ShareLinkType.invite
        return ShareLinkResponse(
            status=True,
            message="Invitation link created successfully",
            data=ShareLinkData(
                type=ShareLinkType.invite,
                code="tPcFcGY2r6b",
                url="https://jlrh8.test-app.link/tPcFcGY2r6b",
            ),
        )

    monkeypatch.setattr("apps.share.router.create_link", _mock_create_link)
    app.dependency_overrides[get_current_app_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    try:
        response = client.post(
            "/api/v1/share/link",
            json={"type": "invite", "user_id": str(uuid4())},
            headers={"Authorization": "Bearer access_test"},
        )
    finally:
        app.dependency_overrides.pop(get_current_app_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["message"] == "Invitation link created successfully"
    assert body["data"]["type"] == "invite"
    assert body["data"]["code"] == "tPcFcGY2r6b"
    assert body["data"]["url"] == "https://jlrh8.test-app.link/tPcFcGY2r6b"


def test_share_link_requires_post_id_for_share() -> None:
    user = SimpleNamespace(id=uuid4(), role="user")

    async def _override_user():
        return user

    async def _override_session():
        yield _NoopSession()

    app.dependency_overrides[get_current_app_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    try:
        response = client.post(
            "/api/v1/share/link",
            json={"type": "share"},
            headers={"Authorization": "Bearer access_test"},
        )
    finally:
        app.dependency_overrides.pop(get_current_app_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert "post_id" in body["message"]


def test_share_link_share_route_success(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), role="user")
    post_id = uuid4()

    async def _override_user():
        return user

    async def _override_session():
        yield _NoopSession()

    async def _mock_create_link(db, user_id, payload):
        assert user_id == user.id
        assert payload.type == ShareLinkType.share
        assert payload.post_id == post_id
        return ShareLinkResponse(
            status=True,
            message="Share link created successfully",
            data=ShareLinkData(
                type=ShareLinkType.share,
                code="shareCode",
                url="https://jlrh8.test-app.link/shareCode",
            ),
        )

    monkeypatch.setattr("apps.share.router.create_link", _mock_create_link)
    app.dependency_overrides[get_current_app_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    try:
        response = client.post(
            "/api/v1/share/link",
            json={"type": "share", "post_id": str(post_id)},
            headers={"Authorization": "Bearer access_test"},
        )
    finally:
        app.dependency_overrides.pop(get_current_app_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["type"] == "share"
    assert body["data"]["code"] == "shareCode"
