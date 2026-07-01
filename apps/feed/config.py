from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FeedSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    hashtag_limit: int = Field(
        default=5,
        ge=1,
        validation_alias=AliasChoices("HASHTAG_LIMIT", "hashtag_limit"),
    )


settings = FeedSettings()
