from __future__ import annotations

import ssl
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse, quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url_env: str = Field(
        default="postgresql://postgres:root@localhost:5432/ksolves",
        alias="DATABASE_URL",
    )

    # Legacy fields to support unit tests (which construct settings using individual fields)
    db_user: str | None = None
    db_password: str | None = None
    db_host: str | None = None
    db_port: int | None = 5432
    db_name: str | None = None
    db_sslmode: str | None = None

    echo_sql: bool = False
    auto_init_db: bool = False

    @property
    def database_url(self) -> str:
        if self.db_user and self.db_password and self.db_host and self.db_name:
            password = quote_plus(str(self.db_password))
            base_url = f"postgresql://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}"
            if self.db_sslmode and self.db_sslmode != "disable":
                return f"{base_url}?sslmode={quote_plus(str(self.db_sslmode))}"
            return base_url
        return self.database_url_env

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            url = url.replace(
                "postgresql://",
                "postgresql+asyncpg://",
                1,
            )
        elif url.startswith("postgres://"):
            url = url.replace(
                "postgres://",
                "postgresql+asyncpg://",
                1,
            )

        # Parse the URL and remove sslmode if present to prevent asyncpg from raising an unexpected argument exception
        parsed = urlparse(url)
        if parsed.query:
            query_params = parse_qs(parsed.query)
            query_params.pop("sslmode", None)
            new_query = urlencode(query_params, doseq=True)
            parsed = parsed._replace(query=new_query)
            url = urlunparse(parsed)

        return url


    @property
    def async_connect_args(self) -> dict:
        if self.db_sslmode == "" or self.db_sslmode == "disable":
            return {}

        # Check if sslmode is specified in the database_url
        parsed = urlparse(self.database_url)
        sslmode = None
        if parsed.query:
            query_params = parse_qs(parsed.query)
            sslmode = query_params.get("sslmode", [None])[0]

        if sslmode == "disable":
            return {}
        elif sslmode in ("require", "verify-ca", "verify-full"):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            return {"ssl": ssl_context}

        # Fallback default behavior: Avoid forcing SSL on localhost to prevent connection crashes locally
        if "localhost" in self.database_url or "127.0.0.1" in self.database_url:
            return {}
            
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        return {"ssl": ssl_context}


# Fixed the trailing dot syntax error here
settings = DatabaseSettings()
