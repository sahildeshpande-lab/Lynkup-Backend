from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_apns_private_key(value: str) -> str:
    """
    Normalize an APNs .p8 private key from env.

    Supports:
    - multiline PEM content
    - single-line values that use literal ``\\n`` escapes
    """
    text = value.strip().strip('"').strip("'")
    if "\\n" in text:
        text = text.replace("\\n", "\n")
    return text.strip()


class ApnsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Apple APNs Authentication Private Key (.p8 content)
    apns_private_key: str | None = Field(default=None, alias="APNS_PRIVATE_KEY")
    # Apple APNs Key ID
    apns_key_id: str | None = Field(default=None, alias="APNS_KEY_ID")
    # Apple Developer Team ID
    apns_team_id: str | None = Field(default=None, alias="APNS_TEAM_ID")
    # iOS Bundle Identifier (App Topic)
    apns_topic: str | None = Field(default=None, alias="APNS_TOPIC")
    # true for development, false for production
    apns_use_sandbox: bool = Field(default=False, alias="APNS_USE_SANDBOX")

    @field_validator(
        "apns_private_key",
        "apns_key_id",
        "apns_team_id",
        "apns_topic",
        mode="before",
    )
    @classmethod
    def blank_to_none(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("apns_private_key", mode="after")
    @classmethod
    def normalize_private_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_apns_private_key(value)
        return normalized or None

    @property
    def is_configured(self) -> bool:
        return bool(
            self.apns_private_key
            and self.apns_key_id
            and self.apns_team_id
            and self.apns_topic
        )


settings = ApnsSettings()
