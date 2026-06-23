from __future__ import annotations

import logging
import os
from html import escape
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
BRAND_COLORS = {
    "brand_green" : "#46B12F",
    "brand_blue" : "#0B5FA5",
    "light_bg" : "#F4F7FB",
    "text_primary" : "#071A35",
    "text_secondary" : "#64748B",
    "footer_bg" : "#071A35",
}


def _sender_is_placeholder(from_email: str) -> bool:
    return from_email.strip().lower() == "no-reply@yourdomain.com"


async def _should_send_to_user(to_email: str) -> bool:
    return True


async def _log_transactional_email(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None, is_sent: bool = False) -> None:
    try:
        from apps.accounts.db_models import TransactionalEmailLog
        from core.database.session import async_session_factory
        from sqlmodel import select
        from sqlalchemy import desc
        from datetime import datetime, timezone

        from_email = os.getenv("SENDGRID_FROM_EMAIL", "no-reply@yourdomain.com")

        async with async_session_factory() as session:
            log_entry = TransactionalEmailLog(
                to=to_email,
                from_email=os.getenv("SENDGRID_FROM_EMAIL"),
                body=html_body,
                attachment=attachment,
                purpose=purpose,
                subject=subject,
                is_sent=is_sent,
                updated_at=datetime.now(timezone.utc),
            )
            session.add(log_entry)
            await session.commit()


    except Exception as e:
        logger.exception("Failed to log transactional email: %s", e)


async def _actually_send_email_via_sendgrid(to_email: str, subject: str, html_body: str, from_email: str) -> bool:
    api_key = os.getenv("SENDGRID_API_KEY")
    if not api_key or not from_email or _sender_is_placeholder(from_email or ""):
        logger.warning("Email send simulated: SendGrid is not fully configured for %s", to_email)
        return True

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
    except Exception:
        logger.exception("Email send skipped: SendGrid client could not be imported")
        return False

    try:
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            html_content=html_body,
        )
        client = SendGridAPIClient(api_key)
        response = client.send(message)
        success = 200 <= response.status_code < 300
        if not success:
            logger.error("SendGrid rejected email to %s with status %s", to_email, getattr(response, "status_code", None))
        return success
    except Exception:
        logger.exception("Email send failed while delivering to %s", to_email)
        return False


async def _send_email(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None) -> bool:
    if not await _should_send_to_user(to_email):
        logger.info("Email send skipped: is_send flag is false for %s", to_email)
        return False

    # Queue the email by inserting with is_sent=False
    await _log_transactional_email(to_email, subject, html_body, purpose, attachment, is_sent=False)
    logger.info("Email queued for %s", to_email)
    return True


async def _send_email_immediately(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None) -> bool:
    """Send the email synchronously via SendGrid first, then log the result to the DB.

    Use this for time-sensitive transactional emails (OTP, password reset, verification)
    where the user is actively waiting. Logging happens AFTER delivery so `is_sent`
    always reflects the actual send outcome.
    """
    if not await _should_send_to_user(to_email):
        logger.info("Email send skipped: is_send flag is false for %s", to_email)
        return False

    from_email = os.getenv("SENDGRID_FROM_EMAIL", "no-reply@yourdomain.com")
    success = await _actually_send_email_via_sendgrid(to_email, subject, html_body, from_email)

    # Log the attempt to DB with the actual send outcome
    await _log_transactional_email(to_email, subject, html_body, purpose, attachment, is_sent=success)

    if success:
        logger.info("Email sent and logged for %s (purpose: %s)", to_email, purpose)
    else:
        logger.error("Email failed to send for %s (purpose: %s) — logged with is_sent=False", to_email, purpose)

    return success


async def cron_send_emails() -> None:
    """Cron task to process and send unsent emails in the transactional email log (limit 20)."""
    import asyncio
    from apps.accounts.db_models import TransactionalEmailLog
    from core.database.session import async_session_factory
    from sqlmodel import select
    from datetime import datetime, timezone

    logger.info("Starting email cron task...")
    try:
        while True:
            async with async_session_factory() as session:
                # Query unsent emails with a limit of 20
                stmt = select(TransactionalEmailLog).where(TransactionalEmailLog.is_sent == False).limit(20)
                unsent_emails = (await session.execute(stmt)).scalars().all()
                
                for email_log in unsent_emails:
                    logger.info("Processing queued email ID %s to %s", email_log.id, email_log.to)
                    success = await _actually_send_email_via_sendgrid(
                        to_email=email_log.to,
                        subject=email_log.subject,
                        html_body=email_log.body,
                        from_email=os.getenv("SENDGRID_FROM_EMAIL"),
                    )
                    if success:
                        email_log.is_sent = True
                        email_log.updated_at = datetime.now(timezone.utc)
                        session.add(email_log)
                        logger.info("Email ID %s successfully sent.", email_log.id)
                
                await session.commit()
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        logger.info("Email cron task cancelled.")
    except Exception as e:
        logger.exception("Failed running email cron task: %s", e)


def _load_template(template_name: str) -> str:
    return (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")


def _render_template(template_name: str, context: dict[str, object], raw_keys: set[str] | None = None) -> str:
    rendered = _load_template(template_name)
    raw_keys = raw_keys or set()
    for key, value in {**BRAND_COLORS, **context}.items():
        replacement = str(value) if key in raw_keys else escape(str(value))
        rendered = rendered.replace(f"{{{{{key}}}}}", replacement)
    return rendered


def _render_email_layout(title: str, body_html: str) -> str:
    base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    logo_path = os.getenv("LOGO_URL", "/static/images/logo.png")
    logo_url = f"{base_url}{logo_path}"
    return _render_template(
        "layouts/base_email.html",
        {"title": title, "body_html": body_html, "logo_url": logo_url},
        raw_keys={"body_html"},
    )


def _paragraphs(text: str) -> str:
    return "".join(f'<p style="margin:0 0 14px;">{escape(part)}</p>' for part in text.splitlines() if part.strip())


def _otp_template_details(otp_purpose: str) -> tuple[str, str, str]:
    match otp_purpose:
        case "email_verification":
            return ("Email Verification", "Email Verification", "To verify your email use this one time OTP")
        case "password_reset":
            return ("Temporary Password", "Your Temporary Password", "Use this as one-time password to set your password")
        case _:
            return ("Email Verification", "Email Verification", "To verify your email use this one time OTP")


def _build_otp_display_html(otp: str, brand_blue: str) -> str:
    """Build the OTP display as a full-width dashed card with large spaced digits.
    Works for any OTP length; digits are space-separated for clarity.
    """
    spaced = " ".join(escape(ch) for ch in otp)
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin: 8px 0 4px;">'
        "<tr>"
        '<td align="center" style="padding: 28px 20px; background-color: #F8FAFC; border: 1.5px dashed #CBD5E1;">'
        f'<span class="otp-font" style="font-size: 32px; font-weight: 700; color: #071A35; letter-spacing: 10px; font-family: \'Courier New\', Courier, monospace; display: inline-block; padding-left: 10px;">{spaced}</span>'
        "</td>"
        "</tr>"
        "</table>"
    )


def build_otp_email_html(otp: str, otp_purpose: str = "email_verification") -> str:
    title, header, body_text = _otp_template_details(otp_purpose)
    otp_expire_minutes =int(os.getenv("OTP_EXPIRE_MINUTES","10"))
    
    brand_blue = str(BRAND_COLORS.get("brand_blue", "#0B5FA5"))
    otp_display_html = _build_otp_display_html(otp, brand_blue)

    raw_keys = {"otp_display"}
    body_html = _render_template(
        "auth/otp_email.html",
        {"otp": otp, "header": header, "body_text": body_text, "otp_display": otp_display_html,"otp_expire_minutes":otp_expire_minutes},
        raw_keys=raw_keys,
    )
    return _render_email_layout(title, body_html)


def _notification_template_name(notification_type: str) -> str:
    match notification_type:
        case "topic":
            return "notification_topic_email.html"
        case "broadcast":
            return "notification_broadcast_email.html"
        case "configuration":
            return "notification_configuration_email.html"
        case "resend-otp":
            return "notification_resend_otp_email.html"
        case "user-creation":
            return "notification_user_creation_email.html"
        case _:
            return "notification_send_email.html"


def build_notification_email_html(
    title: str,
    body: str,
    html_body: str | None = None,
    notification_type: str = "send",
    template_name: str | None = None,
) -> str:
    notification_body = html_body if html_body else _paragraphs(body)
    selected_template = template_name or _notification_template_name(notification_type)
    body_html = _render_template(
        f"notifications/{selected_template}",
        {"title": title, "notification_body": notification_body},
        raw_keys={"notification_body"},
    )
    return _render_email_layout(title, body_html)


def build_account_created_email_html(full_name: str | None = None) -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    body_html = _render_template("auth/account_created_email.html", {"greeting": greeting, "base_url": base_url})
    return _render_email_layout("Account Created Successfully", body_html)


def build_password_changed_email_html(full_name: str | None = None) -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    body_html = _render_template("auth/password_changed_email.html", {"greeting": greeting, "base_url": base_url})
    return _render_email_layout("Password Changed Successfully", body_html)


async def send_otp_email(to_email: str, otp: str, otp_purpose: str = "password_reset") -> bool:
    """Send OTP immediately (send first, then log). Users are actively waiting for this."""
    title, _, _ = _otp_template_details(otp_purpose)
    purpose = f"OTP: {otp_purpose}"
    subject = f"KampuLynk {title}"
    return await _send_email_immediately(
        to_email,
        subject,
        build_otp_email_html(otp, otp_purpose),
        purpose=purpose,
        attachment=otp,
    )


async def send_account_created_email(to_email: str, full_name: str | None = None) -> bool:
    return await _send_email(to_email, "KampuLynk Account Created", build_account_created_email_html(full_name), purpose="Account Created")


async def send_password_changed_email(to_email: str, full_name: str | None = None) -> bool:
    return await _send_email(to_email, "KampuLynk Password Changed", build_password_changed_email_html(full_name), purpose="Password Changed")


# New function to send email verification success notification
async def send_verification_success_email(to_email: str, full_name: str | None = None) -> bool:
    """Send email-verified confirmation immediately (send first, then log).
    Uses the HTML template built by `build_email_verified_success_html`.
    """
    subject = "KampuLynk Email Verified"
    html_content = build_email_verified_success_html(full_name)
    return await _send_email_immediately(to_email, subject, html_content, purpose="Email Verified")


def build_email_verified_success_html(full_name: str | None = None) -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    body_html = (
        f'<div style="font-size:16px;font-weight:700;margin-bottom:16px;">Email Verified Successfully</div>'
        f'<p style="margin:0 0 14px;">{greeting}</p>'
        f'<p style="margin:0 0 14px;">Your email address has been verified successfully.</p>'
        f'<p style="margin:0;">You can now proceed with onboarding.</p>'
    )
    return _render_email_layout("Email Verified Successfully", body_html)



async def send_notification_email(
    to_email: str,
    subject: str,
    title: str,
    body: str,
    html_body: str | None = None,
    notification_type: str = "send",
    template_name: str | None = None,
) -> bool:
    return await _send_email(
        to_email,
        subject,
        build_notification_email_html(title, body, html_body, notification_type, template_name),
        purpose=f"Notification: {notification_type}"
    )


async def send_reset_password_email(to_email: str, reset_link: str) -> bool:
    """Send password reset link immediately (send first, then log). Users are actively waiting for this."""
    subject = "Reset Your Password"
    password_reset_expire_minutes=int(os.getenv("PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", "60"))
    body_html = _render_template(
        "auth/password_reset_email.html",
        {"reset_link": reset_link, "subject": subject , "password_reset_expire_minutes":password_reset_expire_minutes} ,
        raw_keys={"reset_link"},
    )
    html_content = _render_email_layout(subject, body_html)
    return await _send_email_immediately(to_email, subject, html_content, "forget password", attachment=reset_link)
