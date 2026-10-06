from __future__ import annotations

import json
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
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
    post_recognition_milestones: list[int] = Field(
        default=[10],
        validation_alias=AliasChoices(
            "POST_RECOGNITION_MILESTONES",
            "post_recognition_milestones",
        ),
    )

    @field_validator("post_recognition_milestones", mode="before")
    @classmethod
    def parse_post_recognition_milestones(cls, value: object) -> list[int]:
        if value is None or value == "":
            return [10]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return [10]
            if text.startswith("["):
                parsed = json.loads(text)
                if not isinstance(parsed, list):
                    raise ValueError("POST_RECOGNITION_MILESTONES must be a list")
                return [int(item) for item in parsed]
            return [int(part.strip()) for part in text.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [int(item) for item in value]
        raise ValueError("POST_RECOGNITION_MILESTONES must be a list of integers")


settings = EngagementSettings()
