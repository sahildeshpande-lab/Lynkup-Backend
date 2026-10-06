from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppTimezoneSettings(BaseSettings):
    """Shared business-calendar timezone for analytics, invitations, etc."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Canonical env: ANALYTICS_TIMEZONE. INVITATION_TIMEZONE kept as fallback only.
    timezone: str = Field(
        default="America/New_York",
        validation_alias=AliasChoices(
            "ANALYTICS_TIMEZONE",
            "analytics_timezone",
            "INVITATION_TIMEZONE",
            "invitation_timezone",
        ),
    )


app_timezone_settings = AppTimezoneSettings()
