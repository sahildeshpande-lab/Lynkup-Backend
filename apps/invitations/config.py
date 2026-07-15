from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class InvitationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    daily_limit: int = Field(
        default=50,
        validation_alias=AliasChoices("INVITATION_DAILY_LIMIT", "invitation_daily_limit"),
    )
    timezone: str = Field(
        default="Asia/Calcutta",
        validation_alias=AliasChoices("INVITATION_TIMEZONE", "invitation_timezone"),
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


settings = InvitationSettings()
