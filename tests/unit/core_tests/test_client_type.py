"""Unit tests for shared X-Client-Type RSA-OAEP decrypt."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from common.exceptions import ApiError
from core.request_signing import client_type as ct


@pytest.fixture
def rsa_keypair(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    monkeypatch.setattr(ct.auth_settings, "client_type_rsa_private_key_pem", pem)
    monkeypatch.setattr(ct.auth_settings, "client_type_rsa_private_key_path", None)
    monkeypatch.setattr(ct.auth_settings, "client_type_enforce", True)
    ct.clear_client_type_key_cache()
    yield private_key
    ct.clear_client_type_key_cache()


def _encrypt(private_key, plaintext: str) -> str:
    public_key = private_key.public_key()
    ciphertext = public_key.encrypt(
        plaintext.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(ciphertext).decode("ascii")


def test_decrypt_web_and_mobile_roundtrip(rsa_keypair):
    assert ct.decrypt_client_type_ciphertext(_encrypt(rsa_keypair, "web")) == "web"
    assert ct.decrypt_client_type_ciphertext(_encrypt(rsa_keypair, "mobile")) == "mobile"


def test_decrypt_rejects_unknown_plaintext(rsa_keypair):
    with pytest.raises(ct.ClientTypeError):
        ct.decrypt_client_type_ciphertext(_encrypt(rsa_keypair, "desktop"))


def test_require_web_client_type_dependency(rsa_keypair):
    request = MagicMock()
    request.headers = {"X-Client-Type": _encrypt(rsa_keypair, "web")}
    request.state = SimpleNamespace()
    assert ct.require_web_client_type(request) == "web"
    assert request.state.client_type == "web"


def test_require_web_rejects_mobile(rsa_keypair):
    request = MagicMock()
    request.headers = {"X-Client-Type": _encrypt(rsa_keypair, "mobile")}
    request.state = SimpleNamespace()
    with pytest.raises(ApiError):
        ct.require_web_client_type(request)


def test_soft_mode_defaults_to_web_without_key(monkeypatch):
    monkeypatch.setattr(ct.auth_settings, "client_type_rsa_private_key_pem", None)
    monkeypatch.setattr(ct.auth_settings, "client_type_rsa_private_key_path", None)
    monkeypatch.setattr(ct.auth_settings, "client_type_enforce", False)
    ct.clear_client_type_key_cache()
    request = MagicMock()
    request.headers = {}
    request.state = SimpleNamespace()
    assert ct.require_web_client_type(request) == "web"
