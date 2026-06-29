from __future__ import annotations

from core.email.config import EmailSettings, _PLACEHOLDER_FROM_EMAIL


def test_email_settings_detects_placeholder_sender():
    settings = EmailSettings.model_construct(
        sendgrid_api_key="SG.test",
        sendgrid_from_email=_PLACEHOLDER_FROM_EMAIL,
    )
    assert settings.is_placeholder_sender is True
    assert settings.is_sendgrid_configured is False


def test_email_settings_detects_full_sendgrid_config():
    settings = EmailSettings.model_construct(
        sendgrid_api_key="SG.test",
        sendgrid_from_email="verified@example.com",
    )
    assert settings.is_sendgrid_configured is True
