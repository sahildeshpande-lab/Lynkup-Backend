from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AutoModerationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = Field(default=True, alias="AUTO_MODERATION_ENABLED")
    cron_interval_seconds: int = Field(
        default=120,
        ge=1,
        alias="AUTO_MODERATION_CRON_INTERVAL_SECONDS",
    )
    batch_size: int = Field(
        default=50,
        ge=1,
        alias="AUTO_MODERATION_BATCH_SIZE",
    )


settings = AutoModerationSettings()
