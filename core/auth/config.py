from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    access_token_expire_minutes: int = Field(default=1, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    login_event_throttle_seconds: int = Field(default=300, alias="LOGIN_EVENT_THROTTLE_SECONDS")
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(alias="JWT_ALGORITHM")
    access_token_ttl_minutes: int = Field(default=1440, alias="ACCESS_TOKEN_TTL_MINUTES")
    firebase_credentials: str | None = Field(default=None, alias="FIREBASE_CREDENTIALS")
    firebase_service_account_path: str | None = Field(default=None, alias="FIREBASE_SERVICE_ACCOUNT_PATH")
    firebase_project_id: str | None = Field(default=None, alias="FIREBASE_PROJECT_ID")
    firebase_web_api_key: str | None = Field(default=None, alias="FIREBASE_WEB_API_KEY")

    password_reset_token_expire_minutes: int = Field(default=60, alias="PASSWORD_RESET_TOKEN_EXPIRE_MINUTES")
    resend_otp_cooldown_minutes: int = Field(default=2, alias="RESEND_OTP_COOLDOWN_MINUTES")
    otp_expire_minutes: int = Field(default=10, alias="OTP_EXPIRE_MINUTES")
    logo_url: str = Field(default="/static/images/logo.png", alias="LOGO_URL")

    static_otp_email: str = Field(default="avinash.patil@yopmail.com", alias="STATIC_OTP_EMAIL")
    static_otp_code: str = Field(default="1234", alias="STATIC_OTP_CODE")
    max_accounts_per_device: int = Field(default=10, alias="MAX_ACCOUNTS_PER_DEVICE")
    is_disposable_email_enabled: bool = Field(
        default=False,
        alias="IS_DISPOSABLE_EMAIL_ENABLED",
    )

    # Web Admin JWT lifetimes (independent of mobile ACCESS_TOKEN_EXPIRE_MINUTES).
    admin_access_token_expire_minutes: int = Field(
        default=1,
        alias="ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    admin_refresh_token_expire_minutes: int = Field(
        default=10080,
        alias="ADMIN_REFRESH_TOKEN_EXPIRE_MINUTES",
    )

    # Web Admin RSA request signing (RSA-PSS / SHA-256).
    admin_signing_algorithm: str = Field(default="RSA-PSS", alias="ADMIN_SIGNING_ALGORITHM")
    admin_signing_hash: str = Field(default="SHA-256", alias="ADMIN_SIGNING_HASH")
    admin_signing_key_size: int = Field(default=2048, alias="ADMIN_SIGNING_KEY_SIZE")
    admin_signing_timestamp_tolerance_seconds: int = Field(
        default=60,
        alias="ADMIN_SIGNING_TIMESTAMP_TOLERANCE_SECONDS",
    )
    admin_signing_nonce_ttl_seconds: int = Field(
        default=120,
        alias="ADMIN_SIGNING_NONCE_TTL_SECONDS",
    )
    admin_signing_pending_ttl_seconds: int = Field(
        default=600,
        alias="ADMIN_SIGNING_PENDING_TTL_SECONDS",
    )
    # Shared admin rate limit: signed requests, login, key-register, forgot-password.
    # Window also throttles admin session last_seen writes.
    admin_signing_rate_limit_requests: int = Field(
        default=120,
        alias="ADMIN_SIGNING_RATE_LIMIT_REQUESTS",
    )
    admin_signing_rate_limit_window_seconds: int = Field(
        default=60,
        alias="ADMIN_SIGNING_RATE_LIMIT_WINDOW_SECONDS",
    )
    # Comma-separated browser Origins allowed for signed Web Admin requests.
    # Empty list rejects all signed requests.
    admin_allowed_origins: str = Field(default="", alias="ADMIN_ALLOWED_ORIGINS")

    @property
    def admin_allowed_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.admin_allowed_origins.split(",")
            if origin.strip()
        ]

    @field_validator("jwt_algorithm", mode="before")
    @classmethod
    def default_jwt_algorithm(cls, value: str | None) -> str:
        if value is None or not str(value).strip():
            return "HS256"
        return str(value).strip()

    @property
    def firebase_credential_path(self) -> str | None:
        return self.firebase_service_account_path or self.firebase_credentials


settings = AuthSettings()
