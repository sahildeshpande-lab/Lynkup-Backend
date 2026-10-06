from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ShareSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    share_post_token: str | None = Field(default="share-token-added-successfully", alias="SHARE_POST_TOKEN")
    branch_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BRANCH_KEY", "branch_key"),
    )
    branch_api_url: str = Field(
        default="https://api2.branch.io/v1/url",
        validation_alias=AliasChoices("BRANCH_API_URL", "branch_api_url"),
    )


settings = ShareSettings()
