from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from core.database.init import init_db
from core.database.session import async_session_factory
from core.email_service import (
    build_account_created_email_html,
    build_bulk_email_html,
    build_bulk_email_plain_text,
    build_lynkup_response_email_html,
    build_notification_email_html,
    build_otp_email_html,
    build_password_changed_email_html,
    build_password_reset_email_html,
    build_post_review_email_html,
    build_temporary_password_email_html,
    process_pending_emails,
    send_account_created_email,
    send_lynkup_response_email,
    send_notification_email,
    send_password_changed_email,
    send_post_review_email,
    send_reset_password_email,
    send_temporary_password_email,
)


@pytest.fixture(scope="module", autouse=True)
def setup_env():
    os.environ["SENDGRID_API_KEY"] = "mock_sendgrid_key"
    os.environ["SENDGRID_FROM_EMAIL"] = "noreply@example.com"
    yield
    os.environ.pop("SENDGRID_API_KEY", None)
    os.environ.pop("SENDGRID_FROM_EMAIL", None)


@pytest.mark.asyncio
async def test_all_email_builders_and_senders(monkeypatch):
    await init_db()

    # DB-backed email templates are now the runtime source of truth.
    # This test focuses on the email builders/sender queueing flow, so
    # isolate it from the template database.
    async def _render_email_by_name(
        user_id,
        template_name,
        context,
        raw_keys=None,
    ):
        rendered_values = []

        for value in context.values():
            if value is None:
                continue
            if isinstance(value, (str, int, float)):
                rendered_values.append(str(value))

        return (
            context.get("subject") or template_name,
            (
                "<!doctype html>"
                "<html>"
                "<head>"
                f"<title>{context.get('subject', template_name)}</title>"
                "</head>"
                "<body>"
                '<div class="email-container">'
                f"<h1>{template_name}</h1>"
                f"<p>{' '.join(rendered_values)}</p>"
                "</div>"
                "</body>"
                "</html>"
            ),
        )

    monkeypatch.setattr(
        "core.email_service.render_email_by_name",
        _render_email_by_name,
    )

    # 1. Test HTML builders
    html_otp = build_otp_email_html("123456", "email_verification")

    # OTP digits are rendered individually by the legacy HTML builder,
    # so verify that every OTP digit is present rather than requiring
    # the six digits to appear as one contiguous string.
    assert all(digit in html_otp for digit in "123456")
    assert "{{otp}}" not in html_otp

    html_notif = build_notification_email_html("Test Title", "Test Body")
    assert "Test Title" in html_notif
    assert "Test Body" in html_notif

    html_acc = build_account_created_email_html("John")
    assert "Hello John," in html_acc

    html_temp = build_temporary_password_email_html(
        "temp_pass",
        "John",
        "user",
    )
    assert "temp_pass" in html_temp
    assert "Hello John," in html_temp

    html_pwd = build_password_changed_email_html("John")
    assert "Hello John," in html_pwd

    html_lynk = build_lynkup_response_email_html("John", "accepted")
    assert "Hello John," in html_lynk

    html_review = build_post_review_email_html("John", "flag")
    assert "Please Review Your Post" in html_review
    assert "Hello John," in html_review

    html_otp_named = build_otp_email_html(
        "123456",
        "email_verification",
        first_name="John",
    )
    assert "Hello John," in html_otp_named
    assert "Hello John Doe," not in html_otp_named

    html_reset_named = build_password_reset_email_html(
        "http://reset-link",
        first_name="Alex",
    )
    assert "Hello Alex," in html_reset_named
    assert "We received a request to reset the password" in html_reset_named
    assert "text-align:center" in html_reset_named

    # 2. Test senders (which queue or send immediately)
    async with async_session_factory() as session:
        await session.execute(
            text(
                'DELETE FROM transactional_email_log '
                'WHERE "to" LIKE \'test_add_email_%\''
            )
        )
        await session.commit()

    # Queue emails
    res1 = await send_lynkup_response_email(
        "test_add_email_1@example.com",
        "accepted",
        "John",
    )
    assert res1 is True

    res_review = await send_post_review_email(
        "test_add_email_review@example.com",
        "publish",
        "John",
    )
    assert res_review is True

    res2 = await send_temporary_password_email(
        "test_add_email_2@example.com",
        "temp_pass",
        "John",
        "user",
    )
    assert res2 is True

    res3 = await send_password_changed_email(
        "test_add_email_3@example.com",
        "John",
    )
    assert res3 is True

    res4 = await send_notification_email(
        "test_add_email_4@example.com",
        "Subject",
        "Title",
        "Body",
    )
    assert res4 is True

    res5 = await send_account_created_email(
        "test_add_email_5@example.com",
        "John",
    )
    assert res5 is True

    # Immediate email
    res6 = await send_reset_password_email(
        "test_add_email_6@example.com",
        "http://reset",
    )
    assert res6 is True

    # 3. Test cron worker processing pending queued emails
    processed = await process_pending_emails(limit=10)
    assert processed >= 5


def test_build_bulk_email_html_uses_campaign_body():
    html = build_bulk_email_html(
        "Campus update",
        "Hello & welcome",
        "<p>We’re excited to bring you the latest updates from KampuLynk!</p>",
    )

    assert "<title>Campus update</title>" in html
    assert (
        "We’re excited to bring you the latest updates from KampuLynk!"
        in html
    )
    assert "<strong>Name:</strong>" not in html
    assert "<strong>Subject:</strong>" not in html
    assert "Hello &amp; welcome" in html
    assert "email-container" in html
    assert "KampuLynk" in html
    assert "Our mission" not in html
    assert "From your friends at KampuLynk 😊" in html
    assert "automated security notification" not in html
    assert html.lstrip().lower().startswith("<!doctype")

    named = build_bulk_email_html(
        "Campaign Name",
        "Campaign Subject",
        "<p>Hello body</p>",
    )

    assert "<title>Campaign Name</title>" in named
    assert "<p>Hello body</p>" in named
    assert "<strong>Name:</strong> Campaign Name" not in named
    assert "<strong>Subject:</strong> Campaign Subject" not in named
    assert named.lower().count("<html") == 1
    assert "From your friends at KampuLynk 😊" in named
    assert "automated security notification" not in named

    plain = build_bulk_email_plain_text(
        "Campaign Name",
        "Campaign Subject",
        body_text="Hello body",
        body_html="<p>Hello body</p>",
    )

    assert plain == "Hello body"
    assert "Name:" not in plain
    assert "Subject:" not in plain