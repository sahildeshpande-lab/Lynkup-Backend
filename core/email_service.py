from __future__ import annotations

import logging
import os
from html import escape
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
BRAND_COLORS = {
    "crimson": "#E13C4B",
    "deep_navy": "#001E2D",
    "dark_navy": "#000F1E",
    "navy_blue": "#001E3C",
    "medium_blue": "#002D4B",
    "steel_blue": "#1C4587",
    "light_gray": "#E1E1E1",
    "dark_gray": "#4B4B4B",
    "black": "#000000",
}


def _sender_is_placeholder(from_email: str) -> bool:
    return from_email.strip().lower() == "no-reply@yourdomain.com"


async def _should_send_to_user(to_email: str) -> bool:
    return True


async def _log_transactional_email(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None, is_sent: bool = False) -> None:
    try:
        from apps.accounts.db_models import TransactionalEmailLog
        from core.db.session import async_session_factory
        from sqlmodel import select
        from sqlalchemy import desc
        from datetime import datetime, timezone

        from_email = os.getenv("SENDGRID_FROM_EMAIL", "no-reply@yourdomain.com")

        async with async_session_factory() as session:
            log_entry = TransactionalEmailLog(
                to=to_email,
                from_email=from_email,
                body=html_body,
                attachment=attachment,
                purpose=purpose,
                subject=subject,
                is_sent=is_sent,
                updated_at=datetime.now(timezone.utc),
            )
            session.add(log_entry)
            await session.commit()

            # Limit to 50 records
            stmt = select(TransactionalEmailLog).order_by(desc(TransactionalEmailLog.created_at))
            logs = (await session.execute(stmt)).scalars().all()
            if len(logs) > 50:
                logs_to_delete = logs[50:]
                for old_log in logs_to_delete:
                    await session.delete(old_log)
                await session.commit()
    except Exception as e:
        logger.exception("Failed to log transactional email: %s", e)


async def _send_email(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None) -> bool:
    if not await _should_send_to_user(to_email):
        logger.info("Email send skipped: is_send flag is false for %s", to_email)
        return False

    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("SENDGRID_FROM_EMAIL")

    if not api_key or not from_email or _sender_is_placeholder(from_email or ""):
        logger.warning("Email send simulated: SendGrid is not fully configured for %s", to_email)
        success = True
    else:
        success = False
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
        except Exception:
            logger.exception("Email send failed while delivering to %s", to_email)
            success = False

    await _log_transactional_email(to_email, subject, html_body, purpose, attachment, is_sent=success)
    if success:
        logger.info("Email sent and logged for %s", to_email)
    return success


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
    logo_url = f"{base_url}/static/logo.jpg"
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


def build_otp_email_html(otp: str, otp_purpose: str = "email_verification") -> str:
    title, header, body_text = _otp_template_details(otp_purpose)
    body_html = _render_template(
        "auth/otp_email.html",
        {"otp": otp, "header": header, "body_text": body_text},
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
    body_html = _render_template("auth/account_created_email.html", {"greeting": greeting})
    return _render_email_layout("Account Created Successfully", body_html)


def build_password_changed_email_html(full_name: str | None = None) -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    body_html = _render_template("auth/password_changed_email.html", {"greeting": greeting})
    return _render_email_layout("Password Changed Successfully", body_html)


async def send_otp_email(to_email: str, otp: str, otp_purpose: str = "password_reset") -> bool:
    title, _, _ = _otp_template_details(otp_purpose)
    purpose = f"OTP: {otp_purpose}"
    subject = f"KampuLynk {title}"
    return await _send_email(
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
    subject = "forget password"
    body_html = f"""
    <div style="font-size:16px;font-weight:700;margin-bottom:16px;">Forget Password</div>
    <p style="margin:0 0 14px;">To reset your password use this link:</p>
    <p style="text-align: center; margin: 30px 0;">
        <a href="{reset_link}" style="background-color: #000000; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold; display: inline-block;">Reset Password</a>
    </p>
    <p style="margin:0 0 14px;"><a href="{reset_link}">{reset_link}</a></p>
    """
    html_content = _render_email_layout(subject, body_html)
    return await _send_email(to_email, subject, html_content, "forget password", attachment=reset_link)
