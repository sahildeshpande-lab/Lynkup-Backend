import enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CeleryTaskQueue(enum.Enum):
    BACKGROUND_QUEUE = "kampulynk.queue.background"
    BULK_EMAIL_QUEUE = "kampulynk.queue.bulk_email"
    EXPORTS_QUEUE = "kampulynk.queue.exports"
    NOTIFICATIONS_QUEUE = "kampulynk.queue.notifications"
    SPOTLIGHTS_QUEUE = "kampulynk.queue.spotlights"
    TRANSACTIONAL_QUEUE = "kampulynk.queue.transactional"

    @classmethod
    def queue_names_str(cls) -> str:
        return ",".join([str(item.value) for item in cls])


class CelerySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="CELERY_",
    )

    redis_url: str = Field("", description="Redis URL to connect to Redis")
    redis_ssl_enabled: bool = Field(default=False, description="Enable Celery Redis SSL on one worker instance.")
    broker_db: str = Field(default="1", description="Celery broker db")
    redbeat_db: str = Field(default="2", description="Celery redbeat db")
    beat_enabled: bool = Field(
        default=True,
        description="Enable embedded Celery Beat on one worker instance only.",
    )
    worker_pool: str = Field(default="prefork", description="Celery worker pool")
    worker_concurrency: int = Field(
        default=1, ge=1, description="Number of Celery worker execution slots"
    )

    @property
    def broker_url(self):
        return f"{self.redis_url}/{self.broker_db}"

    @property
    def redbeat_url(self):
        return f"{self.redis_url}/{self.redbeat_db}"


settings = CelerySettings()
