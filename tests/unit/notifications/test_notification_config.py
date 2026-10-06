from __future__ import annotations

from apps.notifications.config import NotificationSettings, settings


def test_notification_settings_loads():
    assert isinstance(settings, NotificationSettings)
