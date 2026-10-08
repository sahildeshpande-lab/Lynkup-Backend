"""Production-quality tests for Web Admin RSA request signing + session binding."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.administration.db_models import (
    AdminSession,
    AdminSessionStatus,
    AdminSigningKey,
    AdminSigningKeyStatus,
)
from apps.administration.dependencies import require_signed_admin, require_admin_signed_request
from apps.administration.routes import router as admin_router
from apps.administration.services import signing_canonical as canonical
from apps.administration.services.auth_service import _generate_admin_tokens
from apps.administration.services.signing_crypto import (
    PublicKeyValidationError,
    generate_rsa_key_pair_for_tests,
    normalize_public_key_pem,
    sign_canonical_request_for_tests,
    verify_rsa_pss_signature,
)
from apps.administration.services.signing_service import (
    GENERIC_AUTH_FAILURE,
    RATE_LIMIT_MESSAGE,
    bind_signing_key_after_login,
    register_pending_signing_key,
    verify_signed_admin_request,
)
from apps.administration.services.signing_store import (
    claim_nonce,
    consume_rate_limit,
    pop_pending_public_key,
    store_pending_public_key,
)
from apps.administration.services.session_service import (
    create_admin_session,
    revoke_admin_session,
)
from common.enums import UserStatus
from common.exceptions import ApiError
from core.auth.config import settings as auth_settings
from core.database.session import get_session
from core.security.auth import get_current_admin
from entrypoints.api import app as main_app

TEST_ALLOWED_ORIGIN = "https://admin.test"


class _FakeRedisPipeline:
    def __init__(self, store: "_FakeRedis"):
        self._store = store
        self._ops: list = []

    def zremrangebyscore(self, key, min_score, max_score):
        self._ops.append(("zremrangebyscore", key, min_score, max_score))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def expire(self, key, seconds):
        self._ops.append(("expire", key, seconds))
        return self

    async def execute(self):
        results = []
        now = time.time()
        for op in self._ops:
            kind = op[0]
            if kind == "zremrangebyscore":
                _, key, min_score, max_score = op
                zset = self._store._zsets.setdefault(key, {})
                for member, score in list(zset.items()):
                    if min_score <= score <= max_score:
                        zset.pop(member, None)
                results.append(0)
            elif kind == "zadd":
                _, key, mapping = op
                zset = self._store._zsets.setdefault(key, {})
                for member, score in mapping.items():
                    zset[str(member)] = float(score)
                results.append(len(mapping))
            elif kind == "zcard":
                _, key = op
                results.append(len(self._store._zsets.get(key, {})))
            elif kind == "expire":
                _, key, seconds = op
                self._store._expiry[key] = now + float(seconds)
                results.append(True)
        self._ops.clear()
        return results


class _FakeRedis:
    """Minimal async Redis stand-in for signing-store unit tests."""

    def __init__(self):
        self._kv: dict[str, tuple[str, float | None]] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._expiry: dict[str, float] = {}

    def _expired(self, key: str) -> bool:
        exp = self._expiry.get(key)
        if exp is not None and exp <= time.time():
            self._kv.pop(key, None)
            self._zsets.pop(key, None)
            self._expiry.pop(key, None)
            return True
        return False

    async def set(self, key, value, ex=None, nx=False):
        if self._expired(key):
            pass
        if nx and key in self._kv and not self._expired(key):
            return False
        expires_at = time.time() + float(ex) if ex is not None else None
        self._kv[key] = (str(value), expires_at)
        if expires_at is not None:
            self._expiry[key] = expires_at
        return True

    async def get(self, key):
        if self._expired(key):
            return None
        item = self._kv.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and expires_at <= time.time():
            self._kv.pop(key, None)
            return None
        return value

    async def getdel(self, key):
        value = await self.get(key)
        self._kv.pop(key, None)
        self._expiry.pop(key, None)
        return value

    async def delete(self, *keys):
        for key in keys:
            self._kv.pop(key, None)
            self._zsets.pop(key, None)
            self._expiry.pop(key, None)
        return len(keys)

    def pipeline(self):
        return _FakeRedisPipeline(self)

    async def aclose(self):
        return None


@pytest.fixture(autouse=True)
def _signing_store_isolation(monkeypatch):
    fake_redis = _FakeRedis()
    monkeypatch.setattr(
        "apps.administration.services.signing_store.get_redis_client",
        AsyncMock(return_value=fake_redis),
    )
    monkeypatch.setattr(
        "apps.administration.services.signing_store.close_redis_client",
        AsyncMock(),
    )
    # Isolate from local .env; production so Origin allowlist is enforced in tests.
    monkeypatch.setattr(auth_settings, "environment", "production")
    monkeypatch.setattr(auth_settings, "admin_allowed_origins", TEST_ALLOWED_ORIGIN)
    yield


def _admin_user(**kwargs) -> User:
    user = User(
        id=kwargs.get("id", uuid4()),
        email=kwargs.get("email", "admin@example.com"),
        status=UserStatus.active,
        password_hash=kwargs.get("password_hash", "hash"),
    )
    user.role = kwargs.get("role", "superadmin")
    return user


def _session(
    user_id,
    *,
    session_id=None,
    status=AdminSessionStatus.ACTIVE.value,
    refresh_jti=None,
    expires_at=None,
) -> AdminSession:
    now = datetime.now(timezone.utc)
    return AdminSession(
        id=session_id or uuid4(),
        user_id=user_id,
        status=status,
        refresh_jti=refresh_jti,
        expires_at=expires_at if expires_at is not None else now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
    )


def _signing_key(
    user_id,
    session_id,
    public_pem,
    *,
    status=AdminSigningKeyStatus.ACTIVE.value,
    key_id=None,
):
    return AdminSigningKey(
        id=uuid4(),
        key_id=key_id or uuid4(),
        user_id=user_id,
        session_id=session_id,
        public_key=public_pem,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _mock_request(*, method="GET", path="/api/v1/me", query="", body=b"", headers=None, origin=TEST_ALLOWED_ORIGIN):
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    if origin is not None:
        hdrs["origin"] = origin

    class _Headers(dict):
        def get(self, key, default=None):
            return super().get(key.lower(), default)

    request = MagicMock()
    request.method = method
    request.url = SimpleNamespace(path=path, query=query)
    request.headers = _Headers(hdrs)
    request.client = SimpleNamespace(host="127.0.0.1")
    request.body = AsyncMock(return_value=body)
    request.state = SimpleNamespace()
    return request


def _sign_headers(
    private_key,
    *,
    session_id,
    key_id,
    method="GET",
    path="/api/v1/me",
    query="",
    body=b"",
    timestamp=None,
    nonce=None,
):
    ts = str(timestamp if timestamp is not None else int(time.time()))
    nonce_val = nonce or base64.urlsafe_b64encode(uuid4().bytes).decode("ascii").rstrip("=")
    message = canonical.canonical_request_bytes(
        method=method,
        path=path,
        query_string=query,
        body=body,
        timestamp=ts,
        nonce=nonce_val,
        session_id=str(session_id),
        key_id=str(key_id),
    )
    signature = sign_canonical_request_for_tests(private_key, message)
    return {
        "X-Key-ID": str(key_id),
        "X-Session-Id": str(session_id),
        "X-Timestamp": ts,
        "X-Nonce": nonce_val,
        "X-Signature": signature,
    }, nonce_val


def _db_returning(*records, sticky: bool = True):
    """AsyncSession mock.

    When ``sticky`` is True (default), the last provided record is reused after
    the queue is exhausted so SELECT + UPDATE success paths keep working.
    """
    queue = list(records)
    last = records[-1] if records else None

    async def execute(_statement, *args, **kwargs):
        result = MagicMock()
        if queue:
            value = queue.pop(0)
        else:
            value = last if sticky else None
        result.scalar_one_or_none.return_value = value
        result.scalars.return_value.all.return_value = []
        return result

    db = MagicMock(spec=AsyncSession)
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


# ---------------------------------------------------------------------------
# Identity / JWT / session
# ---------------------------------------------------------------------------


def test_generate_admin_tokens_embeds_independent_session_id():
    user = _admin_user()
    session_id = uuid4()
    access, refresh, jti = _generate_admin_tokens(user, session_id=session_id)
    access_claims = jwt.decode(access, auth_settings.jwt_secret, algorithms=[auth_settings.jwt_algorithm])
    refresh_claims = jwt.decode(refresh, auth_settings.jwt_secret, algorithms=[auth_settings.jwt_algorithm])

    assert access_claims["sub"] == str(user.id)
    assert access_claims["session_id"] == str(session_id)
    assert refresh_claims["session_id"] == str(session_id)
    assert access_claims["session_id"] != access_claims["sub"]
    assert access_claims["session_id"] != refresh
    assert str(user.id) != refresh
    assert refresh_claims["jti"] == jti
    assert "exp" in refresh_claims


@pytest.mark.asyncio
async def test_refresh_preserves_same_session_id():
    from apps.administration.services.auth_service import admin_token
    from apps.accounts.schemas import RefreshTokenRequest

    user = _admin_user()
    session = _session(user.id)
    old_expires = session.expires_at
    access1, refresh1, jti = _generate_admin_tokens(user, session_id=session.id)
    session.refresh_jti = jti
    claims1 = jwt.decode(access1, auth_settings.jwt_secret, algorithms=[auth_settings.jwt_algorithm])

    db = _db_returning(user, session)
    result = await admin_token(RefreshTokenRequest(refreshToken=refresh1), db)
    access2 = result["access_token"]
    claims2 = jwt.decode(access2, auth_settings.jwt_secret, algorithms=[auth_settings.jwt_algorithm])
    refresh2_claims = jwt.decode(
        result["refresh_token"],
        auth_settings.jwt_secret,
        algorithms=[auth_settings.jwt_algorithm],
    )

    assert claims1["session_id"] == claims2["session_id"] == str(session.id)
    assert claims2["session_id"] != str(user.id)
    assert claims2["sub"] == str(user.id)
    assert "refresh_token" in result
    assert session.refresh_jti == refresh2_claims["jti"]
    assert session.refresh_jti != jti
    assert session.expires_at is not None
    # Sliding window: expires_at aligns to access TTL from refresh time (not the prior stub).
    assert session.expires_at != old_expires
    assert session.expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_create_admin_session_is_not_user_id():
    user = _admin_user()
    db = MagicMock(spec=AsyncSession)
    db.add = MagicMock()
    db.flush = AsyncMock()
    session = await create_admin_session(db, user, refresh_jti="abc123")
    assert session.id != user.id
    assert session.user_id == user.id
    assert session.status == AdminSessionStatus.ACTIVE.value
    assert session.refresh_jti == "abc123"
    assert session.expires_at is not None
    assert session.expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_expired_admin_session_rejected_for_api_but_refreshable():
    from apps.administration.services.session_service import (
        get_active_admin_session,
        get_admin_session_for_refresh,
    )

    user = _admin_user()
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    session = _session(user.id, expires_at=past)
    db = _db_returning(session)

    assert await get_active_admin_session(db, session.id, user_id=user.id) is None
    loaded = await get_admin_session_for_refresh(db, session.id, user_id=user.id)
    assert loaded is session



@pytest.mark.asyncio
async def test_revoked_admin_session_is_inactive():
    from apps.administration.services.session_service import get_active_admin_session

    user = _admin_user()
    session = _session(user.id, status=AdminSessionStatus.REVOKED.value)
    db = _db_returning(session)
    loaded = await get_active_admin_session(db, session.id, user_id=user.id)
    assert loaded is None


@pytest.mark.asyncio
async def test_admin_logout_hard_deletes_session_and_keys():
    from apps.administration.services.auth_service import admin_logout

    user = _admin_user()
    session = _session(user.id)
    db = _db_returning(None)
    db.commit = AsyncMock()
    request = SimpleNamespace(state=SimpleNamespace(admin_session=session, admin_session_id=session.id))

    result = await admin_logout(user, db, request=request)

    assert result.status is True
    assert result.data["sessionId"] == str(session.id)
    assert session.status == AdminSessionStatus.REVOKED.value
    # Keys delete + session delete
    assert db.execute.await_count >= 2
    db.commit.assert_awaited()


# ---------------------------------------------------------------------------
# Canonical / crypto
# ---------------------------------------------------------------------------


def test_canonical_session_id_is_not_user_id():
    user_id = uuid4()
    session_id = uuid4()
    built = canonical.build_canonical_request(
        method="GET",
        path="/api/v1/me",
        query_string="",
        body=b"",
        timestamp=1,
        nonce="n",
        session_id=str(session_id),
        key_id="k",
    )
    parts = built.split("\n")
    assert parts[6] == str(session_id)
    assert parts[6] != str(user_id)


def test_canonical_request_empty_body_hash():
    built = canonical.build_canonical_request(
        method="get",
        path="/api/v1/me",
        query_string="",
        body=b"",
        timestamp=1700000000,
        nonce="abc",
        session_id="sid",
        key_id="kid",
    )
    assert built.split("\n")[0] == "GET"
    assert built.split("\n")[3] == hashlib.sha256(b"").hexdigest()


def test_normalize_and_verify_rsa_pss_roundtrip():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    normalized = normalize_public_key_pem(public_pem)
    message = b"canonical-bytes"
    signature = sign_canonical_request_for_tests(private_key, message)
    assert verify_rsa_pss_signature(
        public_key_pem=normalized,
        message=message,
        signature_b64=signature,
    )


def test_normalize_rejects_small_rsa_key(monkeypatch):
    monkeypatch.setattr(auth_settings, "admin_signing_key_size", 2048)
    _, public_pem = generate_rsa_key_pair_for_tests(key_size=1024)
    with pytest.raises(PublicKeyValidationError):
        normalize_public_key_pem(public_pem)


# ---------------------------------------------------------------------------
# Key registration / binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_pending_signing_key_positive():
    _, public_pem = generate_rsa_key_pair_for_tests()
    request = _mock_request(method="POST", path="/api/v1/auth/admin/signing-keys/register")
    response = await register_pending_signing_key(public_pem, request=request)
    assert response.status is True
    key_id = response.data["keyId"]
    stored = await pop_pending_public_key(key_id)
    assert stored is not None


@pytest.mark.asyncio
async def test_bind_signing_key_to_user_and_session():
    _, public_pem = generate_rsa_key_pair_for_tests()
    key_id = uuid4()
    session_id = uuid4()
    await store_pending_public_key(key_id, public_pem)
    user = _admin_user()
    db = _db_returning(None)
    bound = await bind_signing_key_after_login(db, user, key_id, session_id=session_id)
    assert bound == key_id
    created = next(c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], AdminSigningKey))
    assert created.user_id == user.id
    assert created.session_id == session_id
    assert created.key_id == key_id
    assert created.session_id != created.user_id


@pytest.mark.asyncio
async def test_bind_rejects_unknown_registration():
    user = _admin_user()
    db = _db_returning(None)
    with pytest.raises(ApiError):
        await bind_signing_key_after_login(db, user, uuid4(), session_id=uuid4())


# ---------------------------------------------------------------------------
# Verification — positive / cross-session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_signed_request_same_session_succeeds():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    request = _mock_request(headers=headers)
    db = _db_returning(key)
    assert await verify_signed_admin_request(request, user, db, session_id=session.id) is user


@pytest.mark.asyncio
async def test_jwt_s1_with_key_from_s2_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    s1 = _session(user.id)
    s2 = _session(user.id)
    k2 = _signing_key(user.id, s2.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=s1.id, key_id=k2.key_id)
    request = _mock_request(headers=headers)
    db = _db_returning(k2)
    with pytest.raises(ApiError) as exc:
        await verify_signed_admin_request(request, user, db, session_id=s1.id)
    assert exc.value.message == GENERIC_AUTH_FAILURE


@pytest.mark.asyncio
async def test_jwt_s2_with_key_from_s1_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    s1 = _session(user.id)
    s2 = _session(user.id)
    k1 = _signing_key(user.id, s1.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=s2.id, key_id=k1.key_id)
    request = _mock_request(headers=headers)
    db = _db_returning(k1)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(request, user, db, session_id=s2.id)


@pytest.mark.asyncio
async def test_key_belongs_to_another_user_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    other = _admin_user()
    session = _session(user.id)
    key = _signing_key(other.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    request = _mock_request(headers=headers)
    db = _db_returning(key)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(request, user, db, session_id=session.id)


@pytest.mark.asyncio
async def test_same_rsa_key_works_after_jwt_refresh_semantics():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    db = _db_returning(key, key)
    for _ in range(2):
        headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
        request = _mock_request(headers=headers)
        assert await verify_signed_admin_request(request, user, db, session_id=session.id) is user


@pytest.mark.asyncio
async def test_revoked_session_rejects_signed_request_via_loader():
    from apps.administration.services.session_service import get_active_admin_session

    user = _admin_user()
    session = _session(user.id, status=AdminSessionStatus.REVOKED.value)
    db = _db_returning(session)
    loaded = await get_active_admin_session(db, session.id, user_id=user.id)
    assert loaded is None


@pytest.mark.asyncio
async def test_revoke_session_hard_deletes_keys_and_session():
    user = _admin_user()
    session = _session(user.id)
    db = _db_returning(None)
    await revoke_admin_session(db, session, revoke_keys=True, emit_event=True)
    assert session.status == AdminSessionStatus.REVOKED.value
    # signing-key DELETE + session DELETE (event logging may not hit execute)
    assert db.execute.await_count >= 2


# ---------------------------------------------------------------------------
# Negative signing cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_signature_rejected():
    user = _admin_user()
    session = _session(user.id)
    request = _mock_request(
        headers={"X-Key-ID": str(uuid4()), "X-Timestamp": str(int(time.time())), "X-Nonce": "n"}
    )
    db = _db_returning(None)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(request, user, db, session_id=session.id)


@pytest.mark.asyncio
async def test_invalid_signature_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    headers["X-Signature"] = base64.b64encode(b"not-a-real-signature" * 8).decode()
    request = _mock_request(headers=headers)
    db = _db_returning(key)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(request, user, db, session_id=session.id)


@pytest.mark.asyncio
async def test_modified_path_after_signing_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id, path="/api/v1/me")
    request = _mock_request(path="/api/v1/users", headers=headers)
    db = _db_returning(key)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(request, user, db, session_id=session.id)


@pytest.mark.asyncio
async def test_modified_query_and_body_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(
        private_key, session_id=session.id, key_id=key.key_id, query="a=1"
    )
    db = _db_returning(key)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(
            _mock_request(query="a=2", headers=headers),
            user,
            db,
            session_id=session.id,
        )

    headers2, _ = _sign_headers(
        private_key,
        session_id=session.id,
        key_id=key.key_id,
        method="POST",
        body=b'{"ok":true}',
    )
    db2 = _db_returning(key)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(
            _mock_request(method="POST", body=b'{"ok":false}', headers=headers2),
            user,
            db2,
            session_id=session.id,
        )


@pytest.mark.asyncio
async def test_revoked_key_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(
        user.id,
        session.id,
        public_pem,
        status=AdminSigningKeyStatus.REVOKED.value,
    )
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    db = _db_returning(key)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(
            _mock_request(headers=headers),
            user,
            db,
            session_id=session.id,
        )


@pytest.mark.asyncio
async def test_expired_and_future_timestamp_rejected(monkeypatch):
    monkeypatch.setattr(auth_settings, "admin_signing_timestamp_tolerance_seconds", 60)
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    db = _db_returning(key, key)

    stale, _ = _sign_headers(
        private_key,
        session_id=session.id,
        key_id=key.key_id,
        timestamp=int(time.time()) - 120,
    )
    with pytest.raises(ApiError):
        await verify_signed_admin_request(_mock_request(headers=stale), user, db, session_id=session.id)

    future, _ = _sign_headers(
        private_key,
        session_id=session.id,
        key_id=key.key_id,
        timestamp=int(time.time()) + 120,
    )
    with pytest.raises(ApiError):
        await verify_signed_admin_request(_mock_request(headers=future), user, db, session_id=session.id)


@pytest.mark.asyncio
async def test_reused_nonce_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, nonce = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    db = _db_returning(key, key)
    await verify_signed_admin_request(_mock_request(headers=headers), user, db, session_id=session.id)
    headers2, _ = _sign_headers(
        private_key,
        session_id=session.id,
        key_id=key.key_id,
        nonce=nonce,
        timestamp=headers["X-Timestamp"],
    )
    with pytest.raises(ApiError):
        await verify_signed_admin_request(_mock_request(headers=headers2), user, db, session_id=session.id)


@pytest.mark.asyncio
async def test_concurrent_duplicate_nonce_only_one_succeeds():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    db = _db_returning(key, key)

    async def _attempt():
        try:
            await verify_signed_admin_request(
                _mock_request(headers=headers),
                user,
                db,
                session_id=session.id,
            )
            return True
        except ApiError:
            return False

    results = await asyncio.gather(_attempt(), _attempt())
    assert sorted(results) == [False, True]


@pytest.mark.asyncio
async def test_invalid_origin_and_rate_limit(monkeypatch):
    monkeypatch.setattr(auth_settings, "admin_allowed_origins", "https://admin.example.com")
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    db = _db_returning(key)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(
            _mock_request(headers=headers, origin="https://evil.example.com"),
            user,
            db,
            session_id=session.id,
        )

    monkeypatch.setattr(auth_settings, "admin_allowed_origins", TEST_ALLOWED_ORIGIN)
    monkeypatch.setattr(auth_settings, "admin_signing_rate_limit_requests", 1)
    monkeypatch.setattr(auth_settings, "admin_signing_rate_limit_window_seconds", 60)
    db2 = _db_returning(key, key)
    h1, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    await verify_signed_admin_request(_mock_request(headers=h1), user, db2, session_id=session.id)
    h2, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    with pytest.raises(ApiError) as exc:
        await verify_signed_admin_request(_mock_request(headers=h2), user, db2, session_id=session.id)
    assert exc.value.message == RATE_LIMIT_MESSAGE


@pytest.mark.asyncio
async def test_empty_allowed_origins_rejects(monkeypatch):
    monkeypatch.setattr(auth_settings, "environment", "production")
    monkeypatch.setattr(auth_settings, "admin_allowed_origins", "")
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    db = _db_returning(key)
    with pytest.raises(ApiError, match=GENERIC_AUTH_FAILURE):
        await verify_signed_admin_request(
            _mock_request(headers=headers),
            user,
            db,
            session_id=session.id,
        )


@pytest.mark.asyncio
async def test_development_skips_origin_check(monkeypatch):
    monkeypatch.setattr(auth_settings, "environment", "development")
    monkeypatch.setattr(auth_settings, "admin_allowed_origins", "")
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    db = _db_returning(key)
    await verify_signed_admin_request(
        _mock_request(headers=headers, origin=None),
        user,
        db,
        session_id=session.id,
    )


@pytest.mark.asyncio
async def test_rejected_request_does_not_update_last_used_at():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    headers["X-Signature"] = base64.b64encode(b"bad-signature-bytes-bad-signature!!").decode()
    db = _db_returning(key)
    with pytest.raises(ApiError):
        await verify_signed_admin_request(
            _mock_request(headers=headers),
            user,
            db,
            session_id=session.id,
        )
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_nonce_claim_scoped_by_session():
    s1 = uuid4()
    s2 = uuid4()
    nonce = "same-nonce"
    assert await claim_nonce(session_id=s1, nonce=nonce) is True
    assert await claim_nonce(session_id=s1, nonce=nonce) is False
    assert await claim_nonce(session_id=s2, nonce=nonce) is True


@pytest.mark.asyncio
async def test_consume_rate_limit_blocks_after_budget(monkeypatch):
    monkeypatch.setattr(auth_settings, "admin_signing_rate_limit_requests", 2)
    monkeypatch.setattr(auth_settings, "admin_signing_rate_limit_window_seconds", 60)
    session_id = uuid4()
    assert await consume_rate_limit(session_id) is True
    assert await consume_rate_limit(session_id) is True
    assert await consume_rate_limit(session_id) is False


# ---------------------------------------------------------------------------
# Route wiring
# ---------------------------------------------------------------------------


def test_me_route_uses_signed_dependency_and_executes_only_when_valid(monkeypatch):
    business_called = {"value": False}

    async def _fake_admin_me(current_user, db):
        business_called["value"] = True
        return {"status": True, "message": "ok", "data": {"userId": str(current_user.id)}}

    monkeypatch.setattr("apps.administration.routes.services.admin_me", _fake_admin_me)

    app = FastAPI()
    app.include_router(admin_router, prefix="/api/v1")
    admin = _admin_user()

    async def _override_signed():
        return admin

    async def _override_session():
        yield MagicMock(spec=AsyncSession)

    app.dependency_overrides[require_admin_signed_request] = _override_signed
    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    response = client.get("/api/v1/me")
    assert response.status_code == 200
    assert business_called["value"] is True


def test_main_app_me_rejects_unsigned_request():
    async def _override_admin():
        return _admin_user()

    async def _override_session():
        yield _db_returning(None)

    main_app.dependency_overrides[get_current_admin] = _override_admin
    main_app.dependency_overrides[require_signed_admin] = _override_admin
    main_app.dependency_overrides[get_session] = _override_session
    try:
        with patch(
            "apps.administration.routes.services.admin_me",
            side_effect=lambda *a, **k: (_ for _ in ()).throw(AssertionError("business logic ran")),
        ):
            client = TestClient(main_app)
            # Missing request.state.admin_session_id → signing dependency fails before business logic
            response = client.get("/api/v1/me", headers={"Authorization": "Bearer unused"})
            assert response.status_code == 401
            assert response.json().get("message") == GENERIC_AUTH_FAILURE
    finally:
        main_app.dependency_overrides.pop(get_current_admin, None)
        main_app.dependency_overrides.pop(require_signed_admin, None)
        main_app.dependency_overrides.pop(get_session, None)


def test_repo_has_no_user_id_session_fallback():
    """Guardrail: SESSION_ID must not be derived from user.id in signing code."""
    from pathlib import Path

    roots = [
        Path("apps/administration/services/signing_service.py"),
        Path("apps/administration/services/signing_canonical.py"),
        Path("apps/administration/dependencies/request_signing.py"),
        Path("apps/administration/services/auth_service.py"),
    ]
    banned = [
        "session_id=str(current_user.id)",
        "session_id = str(current_user.id)",
        "session_id=current_user.id",
        "session_id = user.id",
        "SESSION_ID = str(current_user.id)",
    ]
    for path in roots:
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{path} still contains {needle!r}"


@pytest.mark.asyncio
async def test_missing_x_session_id_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    headers.pop("X-Session-Id")
    request = _mock_request(headers=headers)
    db = _db_returning(key)
    with pytest.raises(ApiError, match=GENERIC_AUTH_FAILURE):
        await verify_signed_admin_request(request, user, db, session_id=session.id)


@pytest.mark.asyncio
async def test_mismatched_x_session_id_rejected():
    private_key, public_pem = generate_rsa_key_pair_for_tests()
    user = _admin_user()
    session = _session(user.id)
    key = _signing_key(user.id, session.id, public_pem)
    headers, _ = _sign_headers(private_key, session_id=session.id, key_id=key.key_id)
    headers["X-Session-Id"] = str(uuid4())
    request = _mock_request(headers=headers)
    db = _db_returning(key)
    with pytest.raises(ApiError, match=GENERIC_AUTH_FAILURE):
        await verify_signed_admin_request(request, user, db, session_id=session.id)


@pytest.mark.asyncio
async def test_redis_unavailable_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "apps.administration.services.signing_store.get_redis_client",
        AsyncMock(return_value=None),
    )
    with pytest.raises(ApiError, match=GENERIC_AUTH_FAILURE):
        await claim_nonce(session_id=uuid4(), nonce="n1")


@pytest.mark.asyncio
async def test_refresh_token_reuse_revokes_session():
    from apps.administration.services.auth_service import admin_token
    from apps.accounts.schemas import RefreshTokenRequest
    from fastapi import HTTPException

    user = _admin_user()
    session = _session(user.id)
    _access, refresh, _jti = _generate_admin_tokens(user, session_id=session.id)
    session.refresh_jti = "already-rotated-jti"
    db = _db_returning(user, session)
    db.commit = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await admin_token(RefreshTokenRequest(refreshToken=refresh), db)
    assert exc.value.status_code == 401
    assert session.status == AdminSessionStatus.REVOKED.value
    assert session.refresh_jti is None


@pytest.mark.asyncio
async def test_login_rate_limit_blocks_excess(monkeypatch):
    from apps.administration.services.auth_service import admin_signin
    from apps.administration.schemas import AdminLoginRequest
    from fastapi import HTTPException

    monkeypatch.setattr(auth_settings, "admin_signing_rate_limit_requests", 1)
    monkeypatch.setattr(auth_settings, "admin_signing_rate_limit_window_seconds", 300)
    monkeypatch.setattr(auth_settings, "admin_allowed_origins", TEST_ALLOWED_ORIGIN)

    payload = AdminLoginRequest(email="anyone@example.com", password="x")
    request = SimpleNamespace(
        headers={"origin": TEST_ALLOWED_ORIGIN},
        client=SimpleNamespace(host="1.2.3.4"),
        state=SimpleNamespace(),
    )
    db = _db_returning(None)

    first = await admin_signin(payload, db, request=request)
    assert first.status is False

    with pytest.raises(HTTPException) as exc:
        await admin_signin(payload, db, request=request)
    assert exc.value.status_code == 429
