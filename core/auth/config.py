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

    access_token_expire_minutes: int = Field(default=1440, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    recent_auth_max_age_seconds: int = Field(default=300, alias="RECENT_AUTH_MAX_AGE_SECONDS")
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
