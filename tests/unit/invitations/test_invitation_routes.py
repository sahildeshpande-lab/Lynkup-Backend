from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.invitations.config import INVITATION_CODE_MAX_LENGTH
from core.database.session import get_session
from core.security.auth import get_current_user
from entrypoints.api import app


client = TestClient(app)


def _override_current_user():
    user = User(email="jane@example.com", firebase_uid="test-uid")
    user.id = uuid4()
    return user


async def _override_current_user_async():
    return _override_current_user()


class _NoopSession:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("db session should not be used in this route test")

    def add(self, *_args, **_kwargs):
        return None

    async def commit(self):
        return None

    async def refresh(self, *_args, **_kwargs):
        return None


def test_associate_unauthenticated_is_rejected(monkeypatch) -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)
    try:
        response = client.post("/api/v1/invitations/associate", json={"code": "ABSCD"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 401
    body = response.json()
    assert body["status"] is False


def test_associate_rejects_code_exceeding_max_length() -> None:
    app.dependency_overrides[get_current_user] = _override_current_user_async

    async def _override_session():
        yield _NoopSession()

    app.dependency_overrides[get_session] = _override_session
    try:
        response = client.post(
            "/api/v1/invitations/associate",
            json={"code": "X" * (INVITATION_CODE_MAX_LENGTH + 1)},
            headers={"Authorization": "Bearer access_test"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert "code" in body["message"].lower() or "invalid" in body["message"].lower()


def test_associate_route_returns_associated_payload(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), role="user")

    async def _override_user():
        return user

    async def _override_session():
        yield _NoopSession()

    async def _mock_associate(db, user_id, code):
        from apps.invitations.schemas import InvitationAssociateData, InvitationAssociateResponse

        assert user_id == user.id
        assert code == "ABSCD"
        return InvitationAssociateResponse(
            status=True,
            message="Invitation code associated successfully",
            data=InvitationAssociateData(
                id=uuid4(),
                code=code,
                status="ACTIVE",
                redeemed_by_user_id=user_id,
            ),
        )

    monkeypatch.setattr(
        "apps.invitations.routes.associate_invitation",
        _mock_associate,
    )
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    try:
        response = client.post(
            "/api/v1/invitations/associate",
            json={"code": "ABSCD"},
            headers={"Authorization": "Bearer access_test"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["code"] == "ABSCD"
    assert body["data"]["status"] == "ACTIVE"
    assert body["data"]["redeemed_by_user_id"] == str(user.id)


def test_associate_daily_limit_returns_http_200(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), role="user")

    async def _override_user():
        return user

    async def _override_session():
        yield _NoopSession()

    async def _mock_associate(db, user_id, code):
        from apps.invitations.schemas import InvitationAssociateResponse

        return InvitationAssociateResponse(
            status=False,
            message="Daily invitation limit reached",
            data=None,
        )

    monkeypatch.setattr(
        "apps.invitations.routes.associate_invitation",
        _mock_associate,
    )
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    try:
        response = client.post(
            "/api/v1/invitations/associate",
            json={"code": "CODE51"},
            headers={"Authorization": "Bearer access_test"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": False,
        "message": "Daily invitation limit reached",
        "data": None,
    }


def test_redeem_unauthenticated_is_rejected() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_session, None)
    try:
        response = client.post("/api/v1/invitations/redeem", json={"code": "tPcFcGY2r6b"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 401
    body = response.json()
    assert body["status"] is False


def test_redeem_route_uses_authenticated_user(monkeypatch) -> None:
    user = SimpleNamespace(id=uuid4(), role="user")

    async def _override_user():
        return user

    async def _override_session():
        yield _NoopSession()

    async def _mock_redeem(db, *, code, redeemed_by_user_id):
        from apps.invitations.schemas import InvitationRedeemData, InvitationRedeemResponse

        assert redeemed_by_user_id == user.id
        assert code == "tPcFcGY2r6b"
        return InvitationRedeemResponse(
            status=True,
            message="Invitation redeemed successfully",
            data=InvitationRedeemData(
                code=code,
                inviter_user_id=uuid4(),
                redeemed_by_user_id=redeemed_by_user_id,
                redemption_count=1,
                is_converted=True,
            ),
        )

    monkeypatch.setattr(
        "apps.invitations.routes.redeem_invitation_response",
        _mock_redeem,
    )
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    try:
        response = client.post(
            "/api/v1/invitations/redeem",
            json={"code": "tPcFcGY2r6b", "user_id": str(uuid4())},
            headers={"Authorization": "Bearer access_test"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["code"] == "tPcFcGY2r6b"
    assert body["data"]["redeemed_by_user_id"] == str(user.id)
