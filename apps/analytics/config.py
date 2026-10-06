from __future__ import annotations

from common.timezone_settings import app_timezone_settings


class AnalyticsSettings:
    """Analytics settings; timezone is shared via ANALYTICS_TIMEZONE."""

    @property
    def timezone(self) -> str:
        return app_timezone_settings.timezone


settings = AnalyticsSettings()
