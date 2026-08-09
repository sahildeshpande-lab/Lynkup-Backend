from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AccountDeletionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    account_purge_after_days: int = Field(
        default=30,
        alias="ACCOUNT_PURGE_AFTER_DAYS",
        ge=1,
    )
    account_deletion_cron_interval_hours: int = Field(
        default=24,
        alias="ACCOUNT_DELETION_CRON_INTERVAL_HOURS",
        ge=1,
    )
    account_deletion_batch_size: int = Field(
        default=100,
        alias="ACCOUNT_DELETION_BATCH_SIZE",
        ge=1,
        le=1000,
    )


settings = AccountDeletionSettings()
