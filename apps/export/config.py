from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExportSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    export_storage_path: str = Field(
        default="./storage/exports",
        alias="EXPORT_STORAGE_PATH",
    )
    export_retention_days: int = Field(
        default=7,
        ge=1,
        alias="EXPORT_RETENTION_DAYS",
    )
    base_url_export: str = Field(
        default="https://lynkup-backend-311u.onrender.com",
        alias="BASE_URL_EXPORT",
    )


settings = ExportSettings()
