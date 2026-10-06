from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ReconciliationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = Field(default=True, alias="RECONCILIATION_ENABLED")
    interval_seconds: int = Field(
        default=60,
        ge=1,
        alias="RECONCILIATION_INTERVAL_SECONDS",
    )
    batch_size: int = Field(
        default=25,
        ge=1,
        alias="RECONCILIATION_BATCH_SIZE",
    )


settings = ReconciliationSettings()
