from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngagementSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    comment_max_depth: int = Field(
        default=3,
        ge=0,
        le=10,
        validation_alias=AliasChoices("DEFAULT_COMMENT_MAX_DEPTH", "comment_max_depth"),
    )


settings = EngagementSettings()
