from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from entrypoints import api as api_entrypoint
from common.enums import EducationLevel, UserStatus
from common.exceptions import ApiError
from core.auth import dependencies as auth_dependencies
from core.database.config import DatabaseSettings
from core.email.config import EmailSettings
from core.security import admin as admin_security
from core.security import auth as security_auth


def credentials(token: str = "token") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_database_settings_normalize_urls_and_ssl(monkeypatch):
    settings = DatabaseSettings.model_construct(
        database_url_env="postgres://user:pass@localhost:5432/app?sslmode=require&keep=1",
        db_user=None,
        db_password=None,
        db_host=None,
        db_port=5432,
        db_name=None,
        db_sslmode=None,
        echo_sql=False,
        auto_init_db=False,
    )
    assert settings.async_database_url == "postgresql+asyncpg://user:pass@localhost:5432/app?keep=1"
    assert "ssl" in settings.async_connect_args

    discrete = DatabaseSettings.model_construct(
        database_url_env="",
        db_user="user",
        db_password="p@ss word",
        db_host="db.example.test",
        db_port=5433,
        db_name="app",
        db_sslmode="require",
        echo_sql=False,
        auto_init_db=False,
    )
    assert "p%40ss+word" in discrete.database_url
    assert discrete.database_url.endswith("?sslmode=require")
    assert "ssl" in discrete.async_connect_args


def test_email_settings_placeholder_detection():
    placeholder = EmailSettings(SENDGRID_API_KEY="key", SENDGRID_FROM_EMAIL="no-reply@yourdomain.com")
    configured = EmailSettings(SENDGRID_API_KEY="key", SENDGRID_FROM_EMAIL="hello@example.test")
    missing_key = EmailSettings(SENDGRID_API_KEY=None, SENDGRID_FROM_EMAIL="hello@example.test")

    assert placeholder.is_placeholder_sender is True
    assert placeholder.is_sendgrid_configured is False
    assert configured.is_sendgrid_configured is True
    assert missing_key.is_sendgrid_configured is False


def test_enums_and_bearer_token_helpers():
    assert EducationLevel.bachelors.id == 1
    assert EducationLevel.from_id("2") is EducationLevel.masters
    with pytest.raises(ValueError):
        EducationLevel.from_id(999)

    assert security_auth.get_bearer_token(None) is None
    assert security_auth.get_bearer_token(credentials("abc")) == "abc"
    assert security_auth._inactive_account_message(UserStatus.suspended) == "Your account is suspended"

    active_user = SimpleNamespace(status=UserStatus.active, deleted_at=None)
    security_auth._ensure_active_user(active_user)
    pending_user = SimpleNamespace(status=UserStatus.pending, deleted_at=None)
    security_auth._ensure_active_user(pending_user)
    with pytest.raises(ApiError, match="Account doesn't exist"):
        security_auth._ensure_active_user(SimpleNamespace(status=UserStatus.active, deleted_at=datetime.now()))
    with pytest.raises(ApiError, match="Your account is banned"):
        security_auth._ensure_active_user(SimpleNamespace(status=UserStatus.banned, deleted_at=None))
    with pytest.raises(ApiError, match="Your account is suspended"):
        security_auth._ensure_active_user(SimpleNamespace(status=UserStatus.suspended, deleted_at=None))
    with pytest.raises(ApiError, match="Account doesn't exist"):
        security_auth._ensure_active_user(
            SimpleNamespace(status=UserStatus.deleting, deleted_at=None, is_deleted=True)
        )
    with pytest.raises(ApiError, match="Account doesn't exist"):
        security_auth._ensure_active_user(
            SimpleNamespace(status=UserStatus.active, deleted_at=None, is_deleted=True)
        )


@pytest.mark.asyncio
async def test_security_auth_role_dependencies(monkeypatch, mock_db, scalar_result):
    from uuid import uuid4

    from apps.accounts.db_models import User
    from common.enums import UserStatus
    from fastapi.security import HTTPAuthorizationCredentials

    user = SimpleNamespace(role="user")
    moderator = SimpleNamespace(role="moderator")
    viewer = SimpleNamespace(role="viewer")
    superadmin = SimpleNamespace(role="superadmin")

    assert await security_auth.get_current_app_user(user) is user
    with pytest.raises(ApiError, match="Insufficient"):
        await security_auth.get_current_app_user(moderator)

    assert await security_auth.get_current_moderator(moderator) is moderator
    assert await security_auth.get_current_moderator(superadmin) is superadmin
    with pytest.raises(ApiError):
        await security_auth.get_current_moderator(user)

    assert await security_auth.get_current_moderator_or_viewer(viewer) is viewer
    assert await security_auth.get_current_superadmin(superadmin) is superadmin
    with pytest.raises(ApiError):
        await security_auth.get_current_superadmin(moderator)

    # Dual-purpose: app user via local JWT without admin session
    plain = User(id=uuid4(), email="u@example.com", status=UserStatus.active, password_hash="x")
    plain.role = "user"
    monkeypatch.setattr(
        security_auth.jwt,
        "decode",
        lambda token, secret, algorithms: {"type": "access", "sub": str(plain.id)},
    )
    request = SimpleNamespace(state=SimpleNamespace())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    db = mock_db(scalar_result(plain))
    assert await security_auth.get_current_user_or_superadmin(request, creds, db) is plain
    db = mock_db(scalar_result(plain))
    assert await security_auth.get_current_user_moderator_or_superadmin(request, creds, db) is plain

    # Moderator cannot use user_or_superadmin
    mod = User(id=uuid4(), email="m@example.com", status=UserStatus.active, password_hash="x")
    mod.role = "moderator"
    monkeypatch.setattr(
        security_auth.jwt,
        "decode",
        lambda token, secret, algorithms: {"type": "access", "sub": str(mod.id)},
    )
    db = mock_db(scalar_result(mod))
    with pytest.raises(ApiError, match="Insufficient"):
        await security_auth.get_current_user_or_superadmin(request, creds, db)


@pytest.mark.asyncio
async def test_common_route_admin_requires_signed_request(monkeypatch, mock_db, scalar_result):
    """Staff on shared routes must pass client-type + RSA verify; users do not."""
    from uuid import uuid4

    from apps.accounts.db_models import User
    from apps.administration.db_models import AdminSessionStatus
    from apps.administration.services.auth_service import _admin_password_fingerprint
    from common.enums import UserStatus
    from fastapi.security import HTTPAuthorizationCredentials

    password_hash = "hash"
    user_id = uuid4()
    session_id = uuid4()
    admin = User(
        id=user_id,
        email="admin@example.com",
        status=UserStatus.active,
        password_hash=password_hash,
    )
    admin.role = "superadmin"
    admin_session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        status=AdminSessionStatus.ACTIVE.value,
        expires_at=None,
    )
    token_payload = {
        "type": "access",
        "sub": str(user_id),
        "session_id": str(session_id),
        "pf": _admin_password_fingerprint(password_hash),
    }
    monkeypatch.setattr(
        security_auth.jwt,
        "decode",
        lambda token, secret, algorithms: token_payload,
    )

    called = {"signed": False}

    async def _fake_verify(request, current_user, db, *, session_id, skip_origin=False):
        called["signed"] = True
        assert current_user.role == "superadmin"
        return current_user

    monkeypatch.setattr(
        "apps.administration.services.signing_service.verify_signed_admin_request",
        _fake_verify,
    )
    monkeypatch.setattr(
        "core.request_signing.client_type.require_web_client_type",
        lambda request: "web",
    )
    monkeypatch.setattr(
        "core.request_signing.require_web_client_type",
        lambda request: "web",
    )

    class _Headers(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    request = SimpleNamespace(state=SimpleNamespace(), headers=_Headers())
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
    # peek user → get_current_admin user → admin session
    db = mock_db(scalar_result(admin), scalar_result(admin), scalar_result(admin_session))
    result = await security_auth.get_current_user_or_superadmin(request, creds, db)
    assert result is admin
    assert called["signed"] is True

@pytest.mark.asyncio
async def test_security_auth_current_user_local_jwt_and_admin(monkeypatch, mock_db, scalar_result):
    from uuid import uuid4

    from apps.administration.db_models import AdminSessionStatus
    from apps.administration.services.auth_service import _admin_password_fingerprint

    password_hash = "stored-password-hash"
    user_id = uuid4()
    session_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        firebase_uid="firebase-id",
        status=UserStatus.active,
        deleted_at=None,
        role="moderator",
        password_hash=password_hash,
    )
    admin_session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        status=AdminSessionStatus.ACTIVE.value,
    )
    token_payload = {
        "type": "access",
        "sub": str(user_id),
        "session_id": str(session_id),
        "pf": _admin_password_fingerprint(password_hash),
    }
    monkeypatch.setattr(
        security_auth.jwt,
        "decode",
        lambda token, secret, algorithms: token_payload,
    )

    db = mock_db(scalar_result(user))
    assert await security_auth.get_current_user(credentials("local-token"), db) is user

    request = SimpleNamespace(state=SimpleNamespace())
    db = mock_db(scalar_result(user), scalar_result(admin_session))
    assert await security_auth.get_current_admin(request, credentials("local-token"), db) is user
    assert request.state.admin_session_id == session_id

    ordinary = SimpleNamespace(id=uuid4(), status=UserStatus.active, deleted_at=None, role="user")
    db = mock_db(scalar_result(ordinary))
    with pytest.raises(ApiError, match="Insufficient permissions"):
        await security_auth.get_current_admin(
            SimpleNamespace(state=SimpleNamespace()),
            credentials("local-token"),
            db,
        )

    with pytest.raises(ApiError, match="Missing access token"):
        await security_auth.get_current_user(None, mock_db())


@pytest.mark.asyncio
async def test_security_auth_current_user_firebase_fallback(monkeypatch, mock_db, scalar_result):
    monkeypatch.setattr(
        security_auth.jwt,
        "decode",
        lambda token, secret, algorithms: (_ for _ in ()).throw(ValueError("not jwt")),
    )

    import core.auth.services as auth_services

    monkeypatch.setattr(
        auth_services,
        "verify_firebase_token",
        lambda token, check_revoked=False: {"uid": "firebase-id", "email": "user@example.com"},
    )

    user = SimpleNamespace(
        status=UserStatus.active,
        deleted_at=None,
        role="user",
        email="user@example.com",
    )
    db = mock_db(scalar_result(user))
    assert await security_auth.get_current_user(credentials("firebase-token"), db) is user

    stale_user = SimpleNamespace(
        status=UserStatus.active,
        deleted_at=None,
        role="user",
        email="newemail@example.com",
    )
    db = mock_db(scalar_result(stale_user))
    with pytest.raises(ApiError, match="does not match your current email"):
        await security_auth.get_current_user(credentials("firebase-token"), db)

    created = SimpleNamespace(status=UserStatus.active, deleted_at=None, role="user")
    monkeypatch.setattr(security_auth, "complete_firebase_registration", AsyncMock(return_value=created))
    db = mock_db(scalar_result(None))
    assert await security_auth.get_current_user(credentials("new-firebase-token"), db) is created

    monkeypatch.setattr(
        auth_services,
        "verify_firebase_token",
        lambda token, check_revoked=False: (_ for _ in ()).throw(ValueError("bad token")),
    )
    with pytest.raises(ApiError, match="Invalid access token"):
        await security_auth.get_current_user(credentials("bad"), mock_db())


@pytest.mark.asyncio
async def test_security_auth_admin_rejects_bad_jwt_and_inactive_users(monkeypatch, mock_db, scalar_result):
    def _req():
        return SimpleNamespace(state=SimpleNamespace())

    monkeypatch.setattr(
        security_auth.jwt,
        "decode",
        lambda token, secret, algorithms: {"type": "refresh", "sub": "user-id"},
    )
    with pytest.raises(ApiError, match="Invalid access token"):
        await security_auth.get_current_admin(_req(), credentials("refresh-token"), mock_db())

    monkeypatch.setattr(
        security_auth.jwt,
        "decode",
        lambda token, secret, algorithms: {"type": "access", "sub": "user-id", "session_id": "s"},
    )
    with pytest.raises(ApiError, match="User not found"):
        await security_auth.get_current_admin(
            _req(),
            credentials("missing"),
            mock_db(scalar_result(None)),
        )

    deleted = SimpleNamespace(status=UserStatus.active, deleted_at=datetime.now(timezone.utc), role="superadmin")
    with pytest.raises(ApiError, match="Account doesn't exist"):
        await security_auth.get_current_admin(
            _req(),
            credentials("deleted"),
            mock_db(scalar_result(deleted)),
        )

    suspended = SimpleNamespace(status=UserStatus.suspended, deleted_at=None, role="superadmin")
    with pytest.raises(ApiError, match="Your account is suspended"):
        await security_auth.get_current_admin(
            _req(),
            credentials("suspended"),
            mock_db(scalar_result(suspended)),
        )


@pytest.mark.asyncio
async def test_auth_dependencies_firebase_paths(monkeypatch):
    monkeypatch.setattr(auth_dependencies, "verify_firebase_token", lambda token, check_revoked=False: {"uid": token})

    assert await auth_dependencies.get_current_firebase_user(credentials("uid-1")) == {"uid": "uid-1"}
    assert await auth_dependencies.get_current_revoked_checked_firebase_user(credentials("uid-2")) == {"uid": "uid-2"}

    with pytest.raises(HTTPException) as missing:
        auth_dependencies._credentials_or_401(None)
    assert missing.value.status_code == 401

    recent = {"uid": "u", "auth_time": int(datetime.now(timezone.utc).timestamp())}
    assert await auth_dependencies.require_recent_auth(recent) == recent

    stale = {"uid": "u", "auth_time": int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())}
    assert await auth_dependencies.require_recent_auth(stale) == stale
    with pytest.raises(HTTPException):
        await auth_dependencies.require_recent_auth({"uid": "u"})


@pytest.mark.asyncio
async def test_firebase_user_from_payload_uses_body_then_header(monkeypatch):
    seen = []
    monkeypatch.setattr(
        auth_dependencies,
        "verify_firebase_token",
        lambda token, check_revoked=False: seen.append((token, check_revoked)) or {"uid": token},
    )

    request = SimpleNamespace(json=AsyncMock(return_value={"tokenId": "body-token"}), headers={})
    assert await auth_dependencies.get_firebase_user_from_payload(request) == {"uid": "body-token"}

    request = SimpleNamespace(
        json=AsyncMock(side_effect=ValueError("no json")),
        headers={"Authorization": "Bearer header-token"},
    )
    assert await auth_dependencies.get_firebase_user_from_payload(request) == {"uid": "header-token"}
    assert seen == [("body-token", True), ("header-token", True)]

    missing = SimpleNamespace(json=AsyncMock(return_value={}), headers={})
    with pytest.raises(HTTPException, match="401"):
        await auth_dependencies.get_firebase_user_from_payload(missing)


@pytest.mark.asyncio
async def test_admin_cookie_and_session_paths(monkeypatch):
    response = SimpleNamespace(cookies=[], set_cookie=lambda **kwargs: response.cookies.append(kwargs))
    monkeypatch.setattr(
        admin_security,
        "verify_firebase_token",
        lambda token, check_revoked=True: {"role": "superadmin", "firebase": {"sign_in_second_factor": "sms"}},
    )
    monkeypatch.setattr(admin_security.firebase_auth, "create_session_cookie", lambda token, expires_in: f"cookie-{token}")

    result = await admin_security.create_admin_session_cookie(credentials("id-token"), response)
    assert "csrf_token" in result
    assert response.cookies[0]["value"] == "cookie-id-token"

    with pytest.raises(HTTPException) as missing:
        await admin_security.create_admin_session_cookie(None, response)
    assert missing.value.status_code == 401

    monkeypatch.setattr(admin_security.firebase_auth, "verify_session_cookie", lambda cookie, check_revoked=True: {"uid": "admin-uid"})
    monkeypatch.setattr(
        admin_security,
        "get_user_by_firebase_uid",
        AsyncMock(return_value=SimpleNamespace(role="admin")),
    )
    request = SimpleNamespace(
        cookies={"admin_session": "cookie-id-token"},
        headers={"X-CSRF-Token": result["csrf_token"]},
    )
    assert (await admin_security.get_current_admin_user(request, db=object())).role == "admin"

    bad_request = SimpleNamespace(cookies={}, headers={})
    with pytest.raises(HTTPException) as no_cookie:
        await admin_security.get_current_admin_user(bad_request, db=object())
    assert no_cookie.value.status_code == 401


@pytest.mark.asyncio
async def test_inactive_account_errors_return_http_401():
    api_response = await api_entrypoint.api_error_handler(
        None,
        ApiError("Your account is banned"),
    )
    assert api_response.status_code == 401
    assert b'"status":false' in api_response.body.replace(b" ", b"")
    assert b"Your account is banned" in api_response.body

    http_response = await api_entrypoint.legacy_http_exception_handler(
        None,
        HTTPException(status_code=403, detail="Your account is suspended"),
    )
    assert http_response.status_code == 401
    assert b'"status":false' in http_response.body.replace(b" ", b"")
    assert b"Your account is suspended" in http_response.body

    not_found = await api_entrypoint.legacy_http_exception_handler(
        None,
        HTTPException(status_code=404, detail="User not found"),
    )
    assert not_found.status_code == 404


@pytest.mark.asyncio
async def test_auth_errors_return_http_401():
    missing_token = await api_entrypoint.api_error_handler(
        None,
        ApiError("Missing access token"),
    )
    assert missing_token.status_code == 401
    assert b"Missing access token" in missing_token.body

    invalid_token = await api_entrypoint.api_error_handler(
        None,
        ApiError("Invalid access token"),
    )
    assert invalid_token.status_code == 401

    invalid_firebase = await api_entrypoint.api_error_handler(
        None,
        ApiError("Invalid Firebase credentials"),
    )
    assert invalid_firebase.status_code == 401


@pytest.mark.asyncio
async def test_unhandled_errors_return_http_500():
    response = await api_entrypoint.unhandled_exception_handler(
        None,
        RuntimeError("database unavailable"),
    )
    assert response.status_code == 500
    assert b"Internal server error" in response.body

    http_response = await api_entrypoint.legacy_http_exception_handler(
        None,
        HTTPException(status_code=500, detail="Service unavailable"),
    )
    assert http_response.status_code == 500
    assert b"Service unavailable" in http_response.body
