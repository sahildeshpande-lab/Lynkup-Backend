from __future__ import annotations

import os
import pytest
from sqlmodel import select
from sqlalchemy import text

from core.database.session import async_session_factory
from core.database.init import init_db
from core.email_service import (
    build_otp_email_html,
    build_notification_email_html,
    build_account_created_email_html,
    build_temporary_password_email_html,
    build_password_changed_email_html,
    build_post_review_email_html,
    build_lynkup_response_email_html,
    send_lynkup_response_email,
    send_post_review_email,
    send_temporary_password_email,
    send_password_changed_email,
    send_notification_email,
    send_reset_password_email,
    send_account_created_email,
    process_pending_emails,
)


@pytest.fixture(scope="module", autouse=True)
def setup_env():
    os.environ["SENDGRID_API_KEY"] = "mock_sendgrid_key"
    os.environ["SENDGRID_FROM_EMAIL"] = "noreply@example.com"
    yield
    os.environ.pop("SENDGRID_API_KEY", None)
    os.environ.pop("SENDGRID_FROM_EMAIL", None)


@pytest.mark.asyncio
async def test_all_email_builders_and_senders():
    await init_db()

    # 1. Test HTML builders
    html_otp = build_otp_email_html("123456", "email_verification")
    # Digits are rendered in separate <td> cells (not space-joined).
    for digit in "123456":
        assert f">{digit}</td>" in html_otp
    assert "otp-card" in html_otp
    assert 'class="otp-card"' in html_otp and "width:100%" in html_otp

    html_notif = build_notification_email_html("Test Title", "Test Body")
    assert "Test Title" in html_notif
    assert "Test Body" in html_notif

    html_acc = build_account_created_email_html("John Doe")
    assert "John Doe" in html_acc

    html_temp = build_temporary_password_email_html("temp_pass", "John Doe", "user")
    assert "temp_pass" in html_temp
    assert "John Doe" in html_temp

    html_pwd = build_password_changed_email_html("John Doe")
    assert "John Doe" in html_pwd

    html_lynk = build_lynkup_response_email_html("John Doe", "accepted")
    assert "John Doe" in html_lynk

    html_review = build_post_review_email_html("John Doe", "flag")
    assert "Please Review Your Post" in html_review

    # 2. Test senders (which queue or send immediately)
    async with async_session_factory() as session:
        await session.execute(text("DELETE FROM transactional_email_log WHERE \"to\" LIKE 'test_add_email_%'"))
        await session.commit()

    # Queue emails (returns True and inserts into DB with is_sent = False)
    res1 = await send_lynkup_response_email("test_add_email_1@example.com", "accepted", "John Doe")
    assert res1 is True

    res_review = await send_post_review_email("test_add_email_review@example.com", "publish", "John Doe")
    assert res_review is True

    res2 = await send_temporary_password_email("test_add_email_2@example.com", "temp_pass", "John Doe", "user")
    assert res2 is True

    res3 = await send_password_changed_email("test_add_email_3@example.com", "John Doe")
    assert res3 is True

    res4 = await send_notification_email("test_add_email_4@example.com", "Subject", "Title", "Body")
    assert res4 is True

    res5 = await send_account_created_email("test_add_email_5@example.com", "John Doe")
    assert res5 is True

    # Immediate emails (simulate immediate sending, returns True)
    res6 = await send_reset_password_email("test_add_email_6@example.com", "http://reset")
    assert res6 is True

    # 3. Test cron worker processing pending queued emails
    processed = await process_pending_emails(limit=10)
    assert processed >= 5
