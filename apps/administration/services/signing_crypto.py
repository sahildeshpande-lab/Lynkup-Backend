"""RSA-PSS / SHA-256 helpers for Web Admin request signing."""

from __future__ import annotations

import base64
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from core.auth.config import settings as auth_settings

_PEM_PUBLIC_KEY_RE = re.compile(
    r"-----BEGIN PUBLIC KEY-----.+-----END PUBLIC KEY-----",
    re.DOTALL,
)


class PublicKeyValidationError(ValueError):
    """Raised when a client-supplied public key is invalid for admin signing."""


def _expected_key_size() -> int:
    return int(auth_settings.admin_signing_key_size)


def _pss_padding() -> padding.PSS:
    return padding.PSS(
        mgf=padding.MGF1(hashes.SHA256()),
        salt_length=hashes.SHA256().digest_size,
    )


def _b64decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception:
        return base64.b64decode(padded)


def normalize_public_key_pem(public_key: str) -> str:
    """Validate and normalize a browser-exported RSA public key to SPKI PEM."""
    raw = (public_key or "").strip()
    if not raw:
        raise PublicKeyValidationError("Public key is required")

    if _PEM_PUBLIC_KEY_RE.search(raw):
        pem_bytes = raw.encode("utf-8")
    else:
        try:
            der = _b64decode(raw)
        except Exception as exc:
            raise PublicKeyValidationError("Public key must be PEM or base64 SPKI") from exc
        pem_bytes = serialization.load_der_public_key(der).public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    try:
        loaded = serialization.load_pem_public_key(pem_bytes)
    except Exception as exc:
        raise PublicKeyValidationError("Invalid RSA public key") from exc

    if not isinstance(loaded, RSAPublicKey):
        raise PublicKeyValidationError("Public key must be RSA")

    key_size = loaded.key_size
    minimum = _expected_key_size()
    if key_size < minimum:
        raise PublicKeyValidationError(f"RSA key size must be at least {minimum} bits")

    algorithm = (auth_settings.admin_signing_algorithm or "RSA-PSS").upper()
    if algorithm != "RSA-PSS":
        raise PublicKeyValidationError("Unsupported admin signing algorithm")

    hash_name = (auth_settings.admin_signing_hash or "SHA-256").upper().replace("_", "-")
    if hash_name not in {"SHA-256", "SHA256"}:
        raise PublicKeyValidationError("Unsupported admin signing hash")

    return loaded.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def load_public_key(public_key_pem: str) -> RSAPublicKey:
    key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    if not isinstance(key, RSAPublicKey):
        raise PublicKeyValidationError("Public key must be RSA")
    return key


def verify_rsa_pss_signature(*, public_key_pem: str, message: bytes, signature_b64: str) -> bool:
    """Verify an RSA-PSS SHA-256 signature (Web Crypto compatible salt length)."""
    try:
        signature = _b64decode((signature_b64 or "").strip())
        public_key = load_public_key(public_key_pem)
        public_key.verify(signature, message, _pss_padding(), hashes.SHA256())
        return True
    except (InvalidSignature, PublicKeyValidationError, ValueError, TypeError):
        return False


def generate_rsa_key_pair_for_tests(key_size: int | None = None) -> tuple[rsa.RSAPrivateKey, str]:
    """Test helper: return (private_key, public_pem)."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size or _expected_key_size(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_key, public_pem


def sign_canonical_request_for_tests(private_key: rsa.RSAPrivateKey, message: bytes) -> str:
    """Test helper: sign canonical bytes; return standard base64 signature."""
    signature = private_key.sign(message, _pss_padding(), hashes.SHA256())
    return base64.b64encode(signature).decode("ascii")
