"""Foundation tests for mobile request proof (HMAC / nonce / rate limit)."""

from __future__ import annotations

import base64
import hashlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from apps.accounts.db_models import User
from common.enums import UserStatus
from common.exceptions import ApiError
from core.security.mobile import request_proof as proof
from core.security.mobile.config import settings as mobile_settings
from core.security.mobile.rate_limit import (
    RATE_LIMIT_MESSAGE,
    consume_mobile_rate_limit,
)
from core.security.mobile.request_proof import (
    EMPTY_BODY_SHA256,
    HEADER_DEVICE_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    build_canonical_request,
    canonical_request_bytes,
    compute_hmac_signature,
    hash_request_body,
    normalize_query_string,
    resolve_device_hmac_key,
    verify_mobile_request_proof,
)
from core.security.mobile.store import GENERIC_AUTH_FAILURE, claim_nonce


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePipeline:
    def __init__(self, store: "_FakeRedis"):
        self._store = store
        self._ops: list = []

    def zremrangebyscore(self, key, min_score, max_score):
        self._ops.append(("zrem", key, min_score, max_score))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        results = []
        for op in self._ops:
            kind = op[0]
            if kind == "zrem":
                _, key, min_score, max_score = op
                members = self._store.zsets.setdefault(key, {})
                for member, score in list(members.items()):
                    if min_score <= score <= max_score:
                        del members[member]
                results.append(0)
            elif kind == "zadd":
                _, key, mapping = op
                members = self._store.zsets.setdefault(key, {})
                for member, score in mapping.items():
                    members[str(member)] = float(score)
                results.append(len(mapping))
            elif kind == "zcard":
                _, key = op
                results.append(len(self._store.zsets.get(key, {})))
            elif kind == "expire":
                results.append(True)
        self._ops.clear()
        return results


class _FakeRedis:
    def __init__(self):
        self.kv: dict[str, str] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    def pipeline(self):
        return _FakePipeline(self)

    async def aclose(self):
        return None

    async def close(self):
        return None


def _user(**kwargs) -> User:
    user = User(
        id=kwargs.get("id", uuid4()),
        email=kwargs.get("email", "mobile@example.com"),
        status=UserStatus.active,
        password_hash=kwargs.get("password_hash", "hash"),
    )
    user.role = kwargs.get("role", "user")
    return user


def _mock_request(
    *,
    method="POST",
    path="/api/v1/feed",
    query="",
    body=b"",
    headers=None,
):
    hdrs = {str(k).lower(): v for k, v in (headers or {}).items()}

    class _Headers:
        def get(self, name, default=None):
            return hdrs.get(str(name).lower(), default)

    async def _body():
        return body

    url = SimpleNamespace(path=path, query=query)
    return SimpleNamespace(
        method=method,
        url=url,
        headers=_Headers(),
        body=_body,
        state=SimpleNamespace(),
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _sign(
    *,
    key: bytes,
    method="POST",
    path="/api/v1/feed",
    query="",
    body=b"",
    timestamp: int | str,
    nonce: str,
    device_id: str,
) -> str:
    message = canonical_request_bytes(
        method=method,
        path=path,
        query_string=query,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        device_id=device_id,
    )
    return compute_hmac_signature(key=key, message=message)


def _enabled(monkeypatch, *, enabled=True):
    monkeypatch.setattr(mobile_settings, "mobile_security_enabled", enabled)
    monkeypatch.setattr(mobile_settings, "mobile_hmac_timestamp_tolerance_seconds", 60)
    monkeypatch.setattr(mobile_settings, "mobile_hmac_nonce_ttl_seconds", 60)
    monkeypatch.setattr(mobile_settings, "mobile_rate_limit_requests", 120)
    monkeypatch.setattr(mobile_settings, "mobile_rate_limit_window_seconds", 60)


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()

    async def _get():
        return client

    monkeypatch.setattr(
        "core.security.mobile.store.get_redis_client",
        _get,
    )
    monkeypatch.setattr(
        "core.security.mobile.rate_limit.get_redis_client",
        _get,
    )
    monkeypatch.setattr(
        "core.security.mobile.store.close_redis_client",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "core.security.mobile.rate_limit.close_redis_client",
        AsyncMock(),
    )
    return client


# ---------------------------------------------------------------------------
# Canonicalization / body hash
# ---------------------------------------------------------------------------


def test_canonical_request_is_deterministic():
    a = build_canonical_request(
        method="post",
        path="/api/v1/feed",
        query_string="b=2&a=1",
        body=b'{"x":1}',
        timestamp=1700000000,
        nonce="nonce-abc",
        device_id="device-1",
    )
    b = build_canonical_request(
        method="POST",
        path="/api/v1/feed",
        query_string="a=1&b=2",
        body=b'{"x":1}',
        timestamp="1700000000",
        nonce="nonce-abc",
        device_id="device-1",
    )
    assert a == b
    lines = a.split("\n")
    assert lines[0] == "POST"
    assert lines[1] == "/api/v1/feed"
    assert lines[2] == normalize_query_string("b=2&a=1")
    assert lines[3] == hash_request_body(b'{"x":1}')
    assert lines[4] == "1700000000"
    assert lines[5] == "nonce-abc"
    assert lines[6] == "device-1"


def test_body_hash_empty_and_payload():
    assert hash_request_body(b"") == EMPTY_BODY_SHA256
    assert hash_request_body(None) == EMPTY_BODY_SHA256
    payload = b'{"hello":"world"}'
    assert hash_request_body(payload) == hashlib.sha256(payload).hexdigest()


def test_query_normalization_sorts_pairs():
    assert normalize_query_string("z=1&a=2&a=1") == "a=1&a=2&z=1"
    assert normalize_query_string("") == ""
    assert normalize_query_string(None) == ""


# ---------------------------------------------------------------------------
# Feature flag / compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mobile_security_disabled_is_noop(monkeypatch):
    _enabled(monkeypatch, enabled=False)
    user = _user()
    request = _mock_request(headers={})  # missing proof headers
    await verify_mobile_request_proof(request, user)


@pytest.mark.asyncio
async def test_auth_hook_delegates_when_disabled(monkeypatch):
    _enabled(monkeypatch, enabled=False)
    from core.security.auth import _verify_mobile_request_proof

    await _verify_mobile_request_proof(_mock_request(headers={}), _user())


# ---------------------------------------------------------------------------
# Headers / timestamp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_proof_headers_rejected(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(_mock_request(headers={}), _user())
    assert exc.value.message == GENERIC_AUTH_FAILURE


@pytest.mark.asyncio
async def test_valid_timestamp_accepted(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    user = _user()
    device_id = "device-1"
    key = b"per-device-test-key"
    ts = int(time.time())
    nonce = "nonce-valid-ts"
    sig = _sign(key=key, timestamp=ts, nonce=nonce, device_id=device_id)

    monkeypatch.setattr(
        proof,
        "resolve_device_hmac_key",
        AsyncMock(return_value=key),
    )
    request = _mock_request(
        headers={
            HEADER_DEVICE_ID: device_id,
            HEADER_TIMESTAMP: str(ts),
            HEADER_NONCE: nonce,
            HEADER_SIGNATURE: sig,
        }
    )
    await verify_mobile_request_proof(request, user)
    assert request.state.mobile_device_id == device_id


@pytest.mark.asyncio
async def test_expired_timestamp_rejected(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    monkeypatch.setattr(mobile_settings, "mobile_hmac_timestamp_tolerance_seconds", 60)
    user = _user()
    device_id = "device-1"
    key = b"per-device-test-key"
    ts = int(time.time()) - 120
    nonce = "nonce-expired"
    sig = _sign(key=key, timestamp=ts, nonce=nonce, device_id=device_id)
    monkeypatch.setattr(proof, "resolve_device_hmac_key", AsyncMock(return_value=key))

    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(
            _mock_request(
                headers={
                    HEADER_DEVICE_ID: device_id,
                    HEADER_TIMESTAMP: str(ts),
                    HEADER_NONCE: nonce,
                    HEADER_SIGNATURE: sig,
                }
            ),
            user,
        )
    assert exc.value.message == GENERIC_AUTH_FAILURE


@pytest.mark.asyncio
async def test_future_timestamp_rejected(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    user = _user()
    device_id = "device-1"
    key = b"per-device-test-key"
    ts = int(time.time()) + 120
    nonce = "nonce-future"
    sig = _sign(key=key, timestamp=ts, nonce=nonce, device_id=device_id)
    monkeypatch.setattr(proof, "resolve_device_hmac_key", AsyncMock(return_value=key))

    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(
            _mock_request(
                headers={
                    HEADER_DEVICE_ID: device_id,
                    HEADER_TIMESTAMP: str(ts),
                    HEADER_NONCE: nonce,
                    HEADER_SIGNATURE: sig,
                }
            ),
            user,
        )
    assert exc.value.message == GENERIC_AUTH_FAILURE


@pytest.mark.asyncio
async def test_malformed_timestamp_rejected(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(
            _mock_request(
                headers={
                    HEADER_DEVICE_ID: "device-1",
                    HEADER_TIMESTAMP: "not-a-ts",
                    HEADER_NONCE: "n1",
                    HEADER_SIGNATURE: "sig",
                }
            ),
            _user(),
        )
    assert exc.value.message == GENERIC_AUTH_FAILURE


# ---------------------------------------------------------------------------
# Nonce / Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_nonce_claimed_once(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    user_id = uuid4()
    assert await claim_nonce(user_id=user_id, device_id="d1", nonce="n-once") is True
    assert await claim_nonce(user_id=user_id, device_id="d1", nonce="n-once") is False


@pytest.mark.asyncio
async def test_nonce_replay_rejected(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    user = _user()
    device_id = "device-1"
    key = b"per-device-test-key"
    ts = int(time.time())
    nonce = "replay-me"
    sig = _sign(key=key, timestamp=ts, nonce=nonce, device_id=device_id)
    monkeypatch.setattr(proof, "resolve_device_hmac_key", AsyncMock(return_value=key))
    headers = {
        HEADER_DEVICE_ID: device_id,
        HEADER_TIMESTAMP: str(ts),
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: sig,
    }
    await verify_mobile_request_proof(_mock_request(headers=headers), user)
    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(_mock_request(headers=headers), user)
    assert exc.value.message == GENERIC_AUTH_FAILURE


@pytest.mark.asyncio
async def test_redis_unavailable_during_nonce_enforcement(monkeypatch):
    _enabled(monkeypatch)
    monkeypatch.setattr(
        "core.security.mobile.store.get_redis_client",
        AsyncMock(return_value=None),
    )
    user = _user()
    ts = int(time.time())
    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(
            _mock_request(
                headers={
                    HEADER_DEVICE_ID: "device-1",
                    HEADER_TIMESTAMP: str(ts),
                    HEADER_NONCE: "n-redis-down",
                    HEADER_SIGNATURE: "sig",
                }
            ),
            user,
        )
    assert exc.value.message == GENERIC_AUTH_FAILURE


# ---------------------------------------------------------------------------
# HMAC / device binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_per_device_key_fails_closed(monkeypatch, fake_redis):
    """Production resolver returns None — enforcement must reject."""
    _enabled(monkeypatch)
    assert await resolve_device_hmac_key(_user(), "device-1") is None

    ts = int(time.time())
    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(
            _mock_request(
                headers={
                    HEADER_DEVICE_ID: "device-1",
                    HEADER_TIMESTAMP: str(ts),
                    HEADER_NONCE: "n-no-key",
                    HEADER_SIGNATURE: base64.b64encode(b"x" * 32).decode(),
                }
            ),
            _user(),
        )
    assert exc.value.message == GENERIC_AUTH_FAILURE


@pytest.mark.asyncio
async def test_valid_hmac_with_injected_per_device_key(monkeypatch, fake_redis):
    """Verifier algorithm works when a per-device key is supplied via resolver.

    Does not assert that production key provisioning exists (it does not yet).
    """
    _enabled(monkeypatch)
    user = _user()
    device_id = "device-1"
    key = b"injected-device-key"
    ts = int(time.time())
    nonce = "n-valid-hmac"
    body = b'{"ok":true}'
    sig = _sign(
        key=key,
        body=body,
        timestamp=ts,
        nonce=nonce,
        device_id=device_id,
    )
    monkeypatch.setattr(proof, "resolve_device_hmac_key", AsyncMock(return_value=key))
    await verify_mobile_request_proof(
        _mock_request(
            body=body,
            headers={
                HEADER_DEVICE_ID: device_id,
                HEADER_TIMESTAMP: str(ts),
                HEADER_NONCE: nonce,
                HEADER_SIGNATURE: sig,
            },
        ),
        user,
    )


@pytest.mark.asyncio
async def test_invalid_hmac_rejected(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    key = b"per-device-test-key"
    monkeypatch.setattr(proof, "resolve_device_hmac_key", AsyncMock(return_value=key))
    ts = int(time.time())
    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(
            _mock_request(
                headers={
                    HEADER_DEVICE_ID: "device-1",
                    HEADER_TIMESTAMP: str(ts),
                    HEADER_NONCE: "n-bad-sig",
                    HEADER_SIGNATURE: base64.b64encode(b"not-a-real-hmac-digest!!!!").decode(),
                }
            ),
            _user(),
        )
    assert exc.value.message == GENERIC_AUTH_FAILURE


@pytest.mark.asyncio
async def test_wrong_device_id_fails_hmac(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    key = b"per-device-test-key"
    monkeypatch.setattr(proof, "resolve_device_hmac_key", AsyncMock(return_value=key))
    ts = int(time.time())
    nonce = "n-wrong-device"
    # Signature bound to device-A, header claims device-B
    sig = _sign(key=key, timestamp=ts, nonce=nonce, device_id="device-A")
    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(
            _mock_request(
                headers={
                    HEADER_DEVICE_ID: "device-B",
                    HEADER_TIMESTAMP: str(ts),
                    HEADER_NONCE: nonce,
                    HEADER_SIGNATURE: sig,
                }
            ),
            _user(),
        )
    assert exc.value.message == GENERIC_AUTH_FAILURE


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_success(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    monkeypatch.setattr(mobile_settings, "mobile_rate_limit_requests", 3)
    user_id = uuid4()
    assert await consume_mobile_rate_limit(user_id=user_id, device_id="d1") is True
    assert await consume_mobile_rate_limit(user_id=user_id, device_id="d1") is True


@pytest.mark.asyncio
async def test_rate_limit_exceeded(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    monkeypatch.setattr(mobile_settings, "mobile_rate_limit_requests", 2)
    user = _user()
    device_id = "device-rl"
    key = b"per-device-test-key"
    monkeypatch.setattr(proof, "resolve_device_hmac_key", AsyncMock(return_value=key))

    async def _one(nonce: str):
        ts = int(time.time())
        sig = _sign(key=key, timestamp=ts, nonce=nonce, device_id=device_id)
        await verify_mobile_request_proof(
            _mock_request(
                headers={
                    HEADER_DEVICE_ID: device_id,
                    HEADER_TIMESTAMP: str(ts),
                    HEADER_NONCE: nonce,
                    HEADER_SIGNATURE: sig,
                }
            ),
            user,
        )

    await _one("rl-1")
    await _one("rl-2")
    with pytest.raises(ApiError) as exc:
        await _one("rl-3")
    assert exc.value.message == RATE_LIMIT_MESSAGE


@pytest.mark.asyncio
async def test_redis_unavailable_during_rate_limiting(monkeypatch, fake_redis):
    _enabled(monkeypatch)
    user = _user()
    device_id = "device-1"
    key = b"per-device-test-key"
    ts = int(time.time())
    nonce = "n-rl-redis-down"
    sig = _sign(key=key, timestamp=ts, nonce=nonce, device_id=device_id)
    monkeypatch.setattr(proof, "resolve_device_hmac_key", AsyncMock(return_value=key))

    # Nonce succeeds with fake redis; rate-limit Redis is unavailable.
    monkeypatch.setattr(
        "core.security.mobile.rate_limit.get_redis_client",
        AsyncMock(return_value=None),
    )
    with pytest.raises(ApiError) as exc:
        await verify_mobile_request_proof(
            _mock_request(
                headers={
                    HEADER_DEVICE_ID: device_id,
                    HEADER_TIMESTAMP: str(ts),
                    HEADER_NONCE: nonce,
                    HEADER_SIGNATURE: sig,
                }
            ),
            user,
        )
    assert exc.value.message == GENERIC_AUTH_FAILURE


# ---------------------------------------------------------------------------
# Admin path unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_authenticate_request_does_not_require_mobile_headers(
    monkeypatch,
    mock_db,
    scalar_result,
):
    """Staff JWT + session_id takes the web admin branch — mobile proof not required."""
    from core.security import auth as security_auth

    _enabled(monkeypatch, enabled=True)
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

    session = SimpleNamespace(id=session_id, user_id=user_id)

    token_payload = {
        "sub": str(user_id),
        "type": "access",
        "session_id": str(session_id),
    }
    monkeypatch.setattr(
        security_auth.jwt,
        "decode",
        lambda *a, **k: token_payload,
    )

    async def _fake_web_admin(request, credentials, db):
        from core.request_signing import CLIENT_TYPE_WEB, mark_client_type
        from core.security.auth import AuthenticatedRequest

        mark_client_type(request, CLIENT_TYPE_WEB)
        return AuthenticatedRequest(
            client_type=CLIENT_TYPE_WEB,
            user_id=admin.id,
            role=admin.role,
            user=admin,
        )

    monkeypatch.setattr(security_auth, "_authenticate_web_admin", _fake_web_admin)

    # If mobile proof were incorrectly invoked, missing headers would raise.
    mobile_proof = AsyncMock()
    monkeypatch.setattr(security_auth, "_verify_mobile_request_proof", mobile_proof)

    request = _mock_request(headers={})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="admin-tok")
    db = mock_db(scalar_result(admin))

    auth = await security_auth.authenticate_request(request, creds, db)
    assert auth.client_type == "web"
    assert auth.user is admin
    mobile_proof.assert_not_called()
