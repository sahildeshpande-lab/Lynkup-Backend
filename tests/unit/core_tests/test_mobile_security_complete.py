"""Complete mobile security flow tests (device, integrity, attest, pipeline)."""

from __future__ import annotations

import hashlib
import struct
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.accounts.db_models import SecurityEventType, User, UserInstallation
from common.enums import UserStatus
from common.exceptions import ApiError
from core.security.mobile import app_attest as attest_mod
from core.security.mobile import play_integrity as pi
from core.security.mobile.config import settings as mobile_settings
from core.security.mobile.device import bind_mobile_device, load_active_installation
from core.security.mobile.hmac_keys import generate_mobile_hmac_secret, hmac_key_from_installation
from core.security.mobile.pipeline import run_mobile_security_pipeline
from core.security.mobile.play_integrity import validate_integrity_payload
from core.security.mobile.store import GENERIC_AUTH_FAILURE


def _user(**kwargs) -> User:
    user = User(
        id=kwargs.get("id", uuid4()),
        email=kwargs.get("email", "m@example.com"),
        status=UserStatus.active,
        password_hash="h",
    )
    user.role = "user"
    return user


def _installation(user_id, **kwargs) -> UserInstallation:
    return UserInstallation(
        id=kwargs.get("id", uuid4()),
        user_id=user_id,
        device_id=kwargs.get("device_id", "device-1"),
        platform=kwargs.get("platform", "android"),
        is_active=kwargs.get("is_active", True),
        is_device_verified=kwargs.get("is_device_verified", False),
        mobile_hmac_secret=kwargs.get("mobile_hmac_secret"),
        app_attest_key_id=kwargs.get("app_attest_key_id"),
        app_attest_public_key=kwargs.get("app_attest_public_key"),
        app_attest_counter=kwargs.get("app_attest_counter", 0),
        android_package_name=kwargs.get("android_package_name"),
    )


def _request(headers=None, body=b"", method="POST", path="/api/v1/x"):
    hdrs = {str(k).lower(): v for k, v in (headers or {}).items()}

    class _H:
        def get(self, name, default=None):
            return hdrs.get(str(name).lower(), default)

    async def _body():
        return body

    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path, query=""),
        headers=_H(),
        body=_body,
        state=SimpleNamespace(),
        client=SimpleNamespace(host="127.0.0.1"),
    )


@pytest.fixture
def enable_mobile(monkeypatch):
    monkeypatch.setattr(mobile_settings, "mobile_security_enabled", True)
    monkeypatch.setattr(mobile_settings, "android_integrity_enabled", False)
    monkeypatch.setattr(mobile_settings, "ios_attest_enabled", False)


# ---- Device binding ----


@pytest.mark.asyncio
async def test_device_binding_success(mock_db, scalar_result, enable_mobile):
    user = _user()
    inst = _installation(user.id)
    db = mock_db(scalar_result(inst))
    request = _request(headers={"X-Device-Id": "device-1"})
    with patch("core.security.mobile.device.emit_mobile_security_event", AsyncMock()):
        loaded = await load_active_installation(db, user, "device-1", request=request)
    assert loaded.device_id == "device-1"


@pytest.mark.asyncio
async def test_missing_device_id(mock_db, enable_mobile):
    user = _user()
    db = mock_db()
    with patch("core.security.mobile.device.emit_mobile_security_event", AsyncMock()) as emit:
        with pytest.raises(ApiError):
            await load_active_installation(db, user, "", request=_request())
    assert emit.await_args.args[2] == SecurityEventType.MOBILE_MISSING_DEVICE


@pytest.mark.asyncio
async def test_unknown_device(mock_db, scalar_result, enable_mobile):
    user = _user()
    db = mock_db(scalar_result(None), scalar_result(None))
    with patch("core.security.mobile.device.emit_mobile_security_event", AsyncMock()):
        with pytest.raises(ApiError):
            await load_active_installation(db, user, "missing", request=_request())


@pytest.mark.asyncio
async def test_inactive_installation(mock_db, scalar_result, enable_mobile):
    user = _user()
    inst = _installation(user.id, is_active=False)
    db = mock_db(scalar_result(inst))
    with patch("core.security.mobile.device.emit_mobile_security_event", AsyncMock()) as emit:
        with pytest.raises(ApiError):
            await load_active_installation(db, user, "device-1", request=_request())
    assert emit.await_args.args[2] == SecurityEventType.MOBILE_INACTIVE_DEVICE


@pytest.mark.asyncio
async def test_wrong_user_device(mock_db, scalar_result, enable_mobile):
    user = _user()
    # First query by user+device returns None; second finds foreign device id
    db = mock_db(scalar_result(None), scalar_result(uuid4()))
    with patch("core.security.mobile.device.emit_mobile_security_event", AsyncMock()) as emit:
        with pytest.raises(ApiError):
            await load_active_installation(db, user, "foreign", request=_request())
    assert emit.await_args.args[2] == SecurityEventType.UNAUTHORIZED_ACCESS


# ---- DB / HMAC fields ----


def test_android_ios_fields_on_model():
    inst = _installation(uuid4())
    inst.android_package_name = "com.example.app"
    inst.android_certificate_digest = "abc"
    inst.android_integrity_level = "PLAY_RECOGNIZED:MEETS_DEVICE_INTEGRITY"
    inst.android_last_verified_at = datetime.now(timezone.utc)
    inst.app_attest_key_id = "kid"
    inst.app_attest_public_key = "-----BEGIN PUBLIC KEY-----\nX\n-----END PUBLIC KEY-----"
    inst.app_attest_environment = "production"
    inst.app_attest_counter = 3
    inst.app_attest_last_verified_at = datetime.now(timezone.utc)
    secret = generate_mobile_hmac_secret()
    inst.mobile_hmac_secret = secret
    assert hmac_key_from_installation(inst) is not None
    assert inst.is_device_verified is False  # OTP field untouched


# ---- Play Integrity payload validation ----


def test_play_integrity_valid_payload():
    body_hash = hashlib.sha256(b"{}").hexdigest()
    payload = {
        "requestDetails": {"requestHash": body_hash, "requestPackageName": "com.app"},
        "appIntegrity": {
            "packageName": "com.app",
            "certificateSha256Digest": ["aabbcc"],
            "appRecognitionVerdict": "PLAY_RECOGNIZED",
        },
        "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
    }
    level = validate_integrity_payload(
        payload,
        expected_package="com.app",
        expected_request_hash=body_hash,
    )
    assert "PLAY_RECOGNIZED" in level


def test_play_integrity_ignores_certificate_digests():
    """Certificate digests in the token are ignored; package + verdicts are enough."""
    body_hash = hashlib.sha256(b"{}").hexdigest()
    payload = {
        "requestDetails": {"requestHash": body_hash, "requestPackageName": "com.app"},
        "appIntegrity": {
            "packageName": "com.app",
            "certificateSha256Digest": ["deadbeef"],
            "appRecognitionVerdict": "PLAY_RECOGNIZED",
        },
        "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
    }
    level = validate_integrity_payload(
        payload,
        expected_package="com.app",
        expected_request_hash=body_hash,
    )
    assert level.startswith("PLAY_RECOGNIZED:")


def test_play_integrity_wrong_package():
    body_hash = hashlib.sha256(b"").hexdigest()
    payload = {
        "requestDetails": {"requestHash": body_hash},
        "appIntegrity": {
            "packageName": "com.other",
            "certificateSha256Digest": ["aabbcc"],
            "appRecognitionVerdict": "PLAY_RECOGNIZED",
        },
        "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
    }
    with pytest.raises(ApiError, match="package_mismatch"):
        validate_integrity_payload(
            payload,
            expected_package="com.app",
            expected_request_hash=body_hash,
        )


def test_play_integrity_insufficient_verdict():
    body_hash = hashlib.sha256(b"").hexdigest()
    payload = {
        "requestDetails": {"requestHash": body_hash},
        "appIntegrity": {
            "packageName": "com.app",
            "certificateSha256Digest": ["aabbcc"],
            "appRecognitionVerdict": "UNEVALUATED",
        },
        "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_VIRTUAL_INTEGRITY"]},
    }
    with pytest.raises(ApiError, match="app_recognition_rejected"):
        validate_integrity_payload(
            payload,
            expected_package="com.app",
            expected_request_hash=body_hash,
        )


def test_play_integrity_request_hash_mismatch():
    payload = {
        "requestDetails": {"requestHash": "00" * 32},
        "appIntegrity": {
            "packageName": "com.app",
            "certificateSha256Digest": ["aabbcc"],
            "appRecognitionVerdict": "PLAY_RECOGNIZED",
        },
        "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
    }
    with pytest.raises(ApiError, match="request_hash_mismatch"):
        validate_integrity_payload(
            payload,
            expected_package="com.app",
            expected_request_hash="11" * 32,
        )


@pytest.mark.asyncio
async def test_play_integrity_disabled_is_noop(monkeypatch, mock_db):
    monkeypatch.setattr(mobile_settings, "android_integrity_enabled", False)
    user = _user()
    ctx = SimpleNamespace(
        device_id="d1",
        installation=_installation(user.id),
        platform="android",
        user=user,
    )
    await pi.verify_android_play_integrity(
        mock_db(),
        _request(),
        user,
        ctx,  # type: ignore[arg-type]
        body=b"",
    )


@pytest.mark.asyncio
async def test_play_integrity_misconfigured_fails_closed(monkeypatch, mock_db):
    monkeypatch.setattr(mobile_settings, "android_integrity_enabled", True)
    monkeypatch.setattr(mobile_settings, "play_integrity_package_name", "")
    user = _user()
    ctx = SimpleNamespace(
        device_id="d1",
        installation=_installation(user.id),
        platform="android",
        user=user,
    )
    with patch("core.security.mobile.play_integrity.emit_mobile_security_event", AsyncMock()):
        with pytest.raises(ApiError, match="missing_package_name"):
            await pi.verify_android_play_integrity(
                mock_db(),
                _request(headers={"X-Play-Integrity-Token": "tok"}),
                user,
                ctx,  # type: ignore[arg-type]
                body=b"",
            )


# ---- App Attest crypto helpers ----


def test_app_attest_counter_rejects_stale(monkeypatch):
    pytest.importorskip("cbor2")
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    import cbor2
    import base64

    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    bundle = "com.example.app"
    monkeypatch.setattr(mobile_settings, "ios_app_bundle_id", bundle)
    rp = hashlib.sha256(bundle.encode()).digest()
    auth_data = rp + bytes([0x01]) + struct.pack(">I", 5)
    client_hash = hashlib.sha256(b"client").digest()
    sig = key.sign(auth_data + client_hash, ec.ECDSA(hashes.SHA256()))
    assertion = cbor2.dumps({"authenticatorData": auth_data, "signature": sig})
    assertion_b64 = base64.urlsafe_b64encode(assertion).decode().rstrip("=")

    new_counter = attest_mod.verify_assertion_signature(
        public_key_pem=pub,
        assertion_b64=assertion_b64,
        client_data_hash=client_hash,
        expected_rp_id_hash=rp,
        previous_counter=4,
    )
    assert new_counter == 5

    with pytest.raises(ApiError):
        attest_mod.verify_assertion_signature(
            public_key_pem=pub,
            assertion_b64=assertion_b64,
            client_data_hash=client_hash,
            expected_rp_id_hash=rp,
            previous_counter=5,
        )


@pytest.mark.asyncio
async def test_ios_attest_disabled_noop(monkeypatch, mock_db):
    monkeypatch.setattr(mobile_settings, "ios_attest_enabled", False)
    user = _user()
    ctx = SimpleNamespace(
        device_id="d1",
        installation=_installation(user.id, platform="ios"),
        platform="ios",
        user=user,
    )
    await attest_mod.verify_ios_app_attest_assertion(
        mock_db(),
        _request(),
        user,
        ctx,  # type: ignore[arg-type]
        body=b"",
    )


# ---- Pipeline flag compatibility ----


@pytest.mark.asyncio
async def test_pipeline_disabled_compatible(monkeypatch, mock_db, scalar_result):
    monkeypatch.setattr(mobile_settings, "mobile_security_enabled", False)
    user = _user()
    db = mock_db(scalar_result(_installation(user.id)))
    request = _request(headers={"X-Device-Id": "device-1"})
    ctx = await run_mobile_security_pipeline(request, user, db)
    assert ctx.user is user


@pytest.mark.asyncio
async def test_pipeline_enabled_requires_device(monkeypatch, mock_db):
    monkeypatch.setattr(mobile_settings, "mobile_security_enabled", True)
    user = _user()
    with patch("core.security.mobile.device.emit_mobile_security_event", AsyncMock()):
        with pytest.raises(ApiError) as exc:
            await run_mobile_security_pipeline(_request(), _user(), mock_db())
    assert exc.value.message.startswith(GENERIC_AUTH_FAILURE)
    assert "missing_device_id" in exc.value.message


# ---- Audit redaction ----


def test_audit_redacts_secrets():
    from core.security.mobile.audit import _safe_metadata

    cleaned = _safe_metadata(
        {
            "device_id": "d1",
            "integrity_token": "SECRET",
            "assertion": "SECRET",
            "reason": "ok",
        }
    )
    assert cleaned is not None
    assert "integrity_token" not in cleaned
    assert "assertion" not in cleaned
    assert cleaned["device_id"] == "d1"
