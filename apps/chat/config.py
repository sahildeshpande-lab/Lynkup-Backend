from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    stream_api_key: str | None = Field(default=None, alias="STREAM_API_KEY")
    stream_secret_key: str | None = Field(default=None, alias="STREAM_SECRET_KEY")
    stream_token_expiry: int = Field(default=86400, ge=1, alias="STREAM_TOKEN_EXPIRY")


settings = ChatSettings()
