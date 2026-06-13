from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    access_token_expire_minutes: int = Field(default=1440, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS")
    recent_auth_max_age_seconds: int = Field(default=300, alias="RECENT_AUTH_MAX_AGE_SECONDS")
    login_event_throttle_seconds: int = Field(default=300, alias="LOGIN_EVENT_THROTTLE_SECONDS")
    jwt_secret: str = Field(default="ksolves-lynkupproject-authentication", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_ttl_minutes: int = Field(default=1440, alias="ACCESS_TOKEN_TTL_MINUTES")
    firebase_credentials: str | None = Field(default=None, alias="FIREBASE_CREDENTIALS")
    firebase_service_account_path: str | None = Field(default=None, alias="FIREBASE_SERVICE_ACCOUNT_PATH")
    firebase_project_id: str | None = Field(default=None, alias="FIREBASE_PROJECT_ID")

    @property
    def firebase_credential_path(self) -> str | None:
        return self.firebase_service_account_path or self.firebase_credentials


settings = AuthSettings()
