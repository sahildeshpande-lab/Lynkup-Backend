from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_FROM_EMAIL = "no-reply@yourdomain.com"


class EmailSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sendgrid_api_key: str | None = Field(default=None, alias="SENDGRID_API_KEY")
    sendgrid_from_email: str = Field(
        default=_PLACEHOLDER_FROM_EMAIL,
        alias="SENDGRID_FROM_EMAIL",
    )
    sendgrid_from_name: str = Field(
        default="KampuLynk",
        alias="SENDGRID_FROM_NAME",
    )
    base_url: str = Field(default="http://localhost:8000", alias="BASE_URL")
    logo_url: str = Field(default="https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/logo/logo.png",alias="LOGO_URL")
    otp_expire_minutes: int = Field(default=10, alias="OTP_EXPIRE_MINUTES")
    password_reset_token_expire_minutes: int = Field(
        default=60,
        alias="PASSWORD_RESET_TOKEN_EXPIRE_MINUTES",
    )

    @property
    def is_sendgrid_configured(self) -> bool:
        return bool(
            self.sendgrid_api_key
            and self.sendgrid_from_email
            and not self.is_placeholder_sender
        )

    @property
    def is_placeholder_sender(self) -> bool:
        return self.sendgrid_from_email.strip().lower() == _PLACEHOLDER_FROM_EMAIL


settings = EmailSettings()
