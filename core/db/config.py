from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(default="postgresql://postgres:root@localhost:5432/ksolves")
    echo_sql: bool = False
    auto_init_db: bool = False
    seed_dummy_data: bool = False

    @property
    def async_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        return self.database_url


settings = DatabaseSettings()
