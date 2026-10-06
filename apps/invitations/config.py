from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from common.timezone_settings import app_timezone_settings

# Stored invitation codes (generated and FE-associated) share this column width.
INVITATION_CODE_MAX_LENGTH = 64


class InvitationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    daily_limit: int = Field(
        default=50,
        validation_alias=AliasChoices("INVITATION_DAILY_LIMIT", "invitation_daily_limit"),
    )
    block_days: int = Field(
        default=7,
        validation_alias=AliasChoices(
            "INVITATION_BLOCK_DAYS",
            "INVITATION_EXPIRY_DAYS",
            "INVITATION_BLOCK_EMAIL_DAYS",
            "invitation_block_days",
        ),
    )
    code_length: int = Field(
        default=7,
        validation_alias=AliasChoices("INVITATION_CODE_LENGTH", "invitation_code_length"),
    )

    @property
    def timezone(self) -> str:
        """Shared business calendar timezone (ANALYTICS_TIMEZONE)."""
        return app_timezone_settings.timezone


settings = InvitationSettings()
