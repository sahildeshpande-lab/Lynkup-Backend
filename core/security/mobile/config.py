"""Mobile / app request-proof configuration.

Separate from Web Admin RSA signing settings (``ADMIN_SIGNING_*``).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MobileSecuritySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Master switch for mobile request proof + device pipeline on protected routes.
    mobile_security_enabled: bool = Field(default=False, alias="MOBILE_SECURITY_ENABLED")

    mobile_hmac_timestamp_tolerance_seconds: int = Field(
        default=60,
        alias="MOBILE_HMAC_TIMESTAMP_TOLERANCE_SECONDS",
    )
    mobile_hmac_nonce_ttl_seconds: int = Field(
        default=60,
        alias="MOBILE_HMAC_NONCE_TTL_SECONDS",
    )
    mobile_rate_limit_requests: int = Field(
        default=120,
        alias="MOBILE_RATE_LIMIT_REQUESTS",
    )
    mobile_rate_limit_window_seconds: int = Field(
        default=60,
        alias="MOBILE_RATE_LIMIT_WINDOW_SECONDS",
    )

    # Android Play Integrity (required only when ANDROID_INTEGRITY_ENABLED=true).
    # OAuth uses FIREBASE_CREDENTIALS_JSON (or Firebase file-path fallbacks) — do not
    # introduce a separate Play Integrity service-account env var.
    android_integrity_enabled: bool = Field(default=False, alias="ANDROID_INTEGRITY_ENABLED")
    play_integrity_package_name: str = Field(default="", alias="PLAY_INTEGRITY_PACKAGE_NAME")
    play_integrity_certificate_digests: str = Field(
        default="",
        alias="PLAY_INTEGRITY_CERTIFICATE_DIGESTS",
    )

    # iOS App Attest (required only when IOS_ATTEST_ENABLED=true).
    ios_attest_enabled: bool = Field(default=False, alias="IOS_ATTEST_ENABLED")
    ios_app_bundle_id: str = Field(default="", alias="IOS_APP_BUNDLE_ID")
    ios_app_attest_environment: str = Field(
        default="production",
        alias="IOS_APP_ATTEST_ENVIRONMENT",
    )
    ios_attest_challenge_ttl_seconds: int = Field(
        default=120,
        alias="IOS_ATTEST_CHALLENGE_TTL_SECONDS",
    )

    @property
    def play_integrity_certificate_digest_list(self) -> list[str]:
        return [
            part.strip().lower().replace(":", "")
            for part in self.play_integrity_certificate_digests.split(",")
            if part.strip()
        ]

    @field_validator("ios_app_attest_environment", mode="before")
    @classmethod
    def normalize_attest_env(cls, value: str | None) -> str:
        raw = (value or "production").strip().lower()
        if raw in {"production", "prod"}:
            return "production"
        if raw in {"development", "dev", "sandbox"}:
            return "development"
        return raw


settings = MobileSecuritySettings()
