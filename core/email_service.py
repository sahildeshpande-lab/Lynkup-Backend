from __future__ import annotations

import os
import logging
from html import escape
from pathlib import Path
from datetime import datetime, timezone

from fastapi import BackgroundTasks
from uuid import UUID

from core.email.config import _PLACEHOLDER_FROM_EMAIL, settings as email_settings

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
    return from_email.strip().lower() == _PLACEHOLDER_FROM_EMAIL


async def _should_send_to_user(to_email: str) -> bool:
    return True


def _from_email() -> str:
    return email_settings.sendgrid_from_email


async def _log_transactional_email(
    to_email: str,
    subject: str,
    html_body: str | None = None,
    purpose: str = "",
    attachment: str | None = None,
    is_sent: bool | None = None,
    *,
    content: str | None = None,
    is_send: bool | None = None,
    # sent_at: datetime | None = None,
    # error_message: str | None = None,
) -> None:
    """Create one transactional email log entry.

    ``attachment`` is accepted for backward compatibility but intentionally not
    persisted; logs store only rendered content and metadata.
    """
    try:
        from apps.accounts.db_models import TransactionalEmailLog
        from core.database.session import async_session_factory

        rendered_content = content if content is not None else html_body
        if rendered_content is None:
            rendered_content = ""
        send_status = is_send if is_send is not None else bool(is_sent)

        async with async_session_factory() as session:
            log_entry = TransactionalEmailLog(
                to_email=to_email,
                from_email=_from_email(),
                content=rendered_content,
                purpose=purpose,
                subject=subject,
                is_send=send_status,
                # sent_at=sent_at,
                # error_message=error_message,
                attachment=attachment,
                updated_at=datetime.now(timezone.utc),
            )
            session.add(log_entry)
            await session.commit()


    except Exception as e:
        logger.exception("Failed to log transactional email: %s", e)


async def _deliver_email_via_sendgrid(to_email: str, subject: str, html_body: str, from_email: str) -> tuple[bool, str | None]:
    api_key = email_settings.sendgrid_api_key
    if not api_key or not from_email or _sender_is_placeholder(from_email or ""):
        logger.warning("Email send simulated: SendGrid is not fully configured for %s", to_email)
        return True, None

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
    except Exception:
        message = "SendGrid client could not be imported"
        logger.exception("Email send skipped: %s", message)
        return False, message

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
            message = f"SendGrid rejected email with status {getattr(response, 'status_code', None)}"
            logger.error("%s for %s", message, to_email)
            return False, message
        return True, None
    except Exception as exc:
        message = str(exc)
        exc_name = type(exc).__name__
        if "UnauthorizedError" in exc_name or "401" in message or "Unauthorized" in message:
            logger.warning(
                "SendGrid API Key is unauthorized or invalid (401). "
                "Simulating email delivery. Email details:\n"
                "To: %s\n"
                "Subject: %s\n"
                "Body:\n%s\n",
                to_email,
                subject,
                html_body,
            )
            return True, None
        logger.exception("Email send failed while delivering to %s", to_email)
        return False, message


async def _actually_send_email_via_sendgrid(to_email: str, subject: str, html_body: str, from_email: str) -> bool:
    success, _ = await _deliver_email_via_sendgrid(to_email, subject, html_body, from_email)
    return success


async def _send_and_log_email(
    to_email: str,
    subject: str,
    html_body: str,
    purpose: str,
    from_email: str | None = None,
    log_id: UUID | None = None,
) -> bool:
    """Core function to actually deliver an email and log/update its transaction status.
    
    If `log_id` is provided, it updates the existing log record (used by cron).
    Otherwise, it creates a new successful/failed log record (used by immediate).
    """
    from_email = from_email or _from_email()
    if not await _should_send_to_user(to_email):
        logger.info("Email send skipped: is_send flag is false for %s", to_email)
        return False

    success = await _actually_send_email_via_sendgrid(to_email, subject, html_body, from_email)
    error_message = None if success else "Failed to send email"

    try:
        from apps.accounts.db_models import TransactionalEmailLog
        from core.database.session import async_session_factory
        from sqlmodel import select
        
        async with async_session_factory() as session:
            if log_id:
                # Update existing log
                stmt = select(TransactionalEmailLog).where(TransactionalEmailLog.id == log_id).with_for_update()
                db_log = (await session.execute(stmt)).scalars().first()
                if db_log:
                    db_log.is_send = success
                    # db_log.sent_at = datetime.now(timezone.utc) if success else None
                    # db_log.error_message = error_message
                    db_log.updated_at = datetime.now(timezone.utc)
                    session.add(db_log)
                    await session.commit()
                    if success:
                        logger.info("Email ID %s successfully sent to %s.", log_id, to_email)
                    else:
                        logger.warning("Email ID %s failed to send to %s: %s", log_id, to_email, error_message)
            else:
                # Create a new log entry
                log_entry = TransactionalEmailLog(
                    to_email=to_email,
                    from_email=from_email,
                    content=html_body,
                    purpose=purpose,
                    subject=subject,
                    is_send=success,
                    # sent_at=datetime.now(timezone.utc) if success else None,
                    # error_message=error_message,
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(log_entry)
                await session.commit()
                if success:
                    logger.info("Immediate email successfully sent and logged for %s", to_email)
                else:
                    logger.error("Immediate email failed to send and logged for %s: %s", to_email, error_message)
    except Exception as e:
        logger.exception("Failed to write/update transactional email log: %s", e)

    return success


def send_email_in_background(
    background_tasks: BackgroundTasks | None,
    to_email: str,
    subject: str,
    html_body: str,
    purpose: str,
    from_email: str | None = None,
) -> None:
    """Helper to run the send_and_log function in background if background_tasks is provided,
    otherwise executes in a non-blocking asyncio task.
    """
    if background_tasks:
        background_tasks.add_task(
            _send_and_log_email,
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            purpose=purpose,
            from_email=from_email,
        )
        logger.info("Email queued via FastAPI BackgroundTasks for %s", to_email)
    else:
        import asyncio
        asyncio.create_task(
            _send_and_log_email(
                to_email=to_email,
                subject=subject,
                html_body=html_body,
                purpose=purpose,
                from_email=from_email,
            )
        )
        logger.info("Email queued via asyncio background task for %s", to_email)


async def _send_email(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None) -> bool:
    if not await _should_send_to_user(to_email):
        logger.info("Email send skipped: is_send flag is false for %s", to_email)
        return False

    await _queue_email(to_email, subject, html_body, purpose, attachment)
    logger.info("Email queued for %s", to_email)
    return True


async def _queue_email(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None) -> bool:
    if not await _should_send_to_user(to_email):
        logger.info("Email queue skipped: is_send flag is false for %s", to_email)
        return False
    await _log_transactional_email(
        to_email=to_email,
        subject=subject,
        content=html_body,
        purpose=purpose,
        is_send=False,
        attachment=attachment,
    )
    return True


async def _send_email_immediately(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None) -> bool:
    """Legacy synchronous email send compatibility method."""
    return await _send_and_log_email(to_email, subject, html_body, purpose)


async def process_pending_emails(limit: int = 10) -> int:
    from apps.accounts.db_models import TransactionalEmailLog
    from core.database.session import async_session_factory
    from sqlmodel import select

    logger.info("Cron started: processing pending emails (limit: %d)...", limit)

    async with async_session_factory() as session:
        stmt = (
            select(TransactionalEmailLog)
            .where(TransactionalEmailLog.is_send == False)
            .order_by(TransactionalEmailLog.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        pending_emails = (await session.execute(stmt)).scalars().all()
        email_ids = [email.id for email in pending_emails]

    if not pending_emails:
        logger.info("Cron finished: no pending emails to process.")
        return 0

    logger.info("Cron processing %d emails: %s", len(email_ids), email_ids)

    processed = 0
    success_count = 0
    failure_count = 0

    for email_log in pending_emails:
        try:
            logger.info("Processing queued email ID %s to %s", email_log.id, email_log.to_email)
            success = await _send_and_log_email(
                to_email=email_log.to_email,
                subject=email_log.subject,
                html_body=email_log.content,
                purpose=email_log.purpose,
                from_email=email_log.from_email or _from_email(),
                log_id=email_log.id,
            )
            processed += 1
            if success:
                success_count += 1
            else:
                failure_count += 1
        except Exception as e:
            failure_count += 1
            logger.exception("Unexpected failure processing email ID %s: %s", email_log.id, e)
            try:
                async with async_session_factory() as fail_session:
                    stmt = select(TransactionalEmailLog).where(TransactionalEmailLog.id == email_log.id).with_for_update()
                    db_log = (await fail_session.execute(stmt)).scalars().first()
                    if db_log:
                        db_log.error_message = f"Unexpected error: {str(e)}"
                        db_log.updated_at = datetime.now(timezone.utc)
                        fail_session.add(db_log)
                        await fail_session.commit()
            except Exception as db_exc:
                logger.error("Failed to log unexpected error for email ID %s to database: %s", email_log.id, db_exc)

    logger.info(
        "Cron finished: processed %d emails. Successes: %d, Failures: %d.",
        processed,
        success_count,
        failure_count,
    )
    return processed


async def cron_send_emails() -> None:
    """Cron task to process unsent transactional emails once per minute."""
    import asyncio

    logger.info("Starting email cron task...")
    try:
        while True:
            await process_pending_emails(limit=10)
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


_DEFAULT_LOGO_URL = "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/logo/logo.png"


def _resolve_logo_url() -> str:
    # Prefer EmailSettings (loads .env via pydantic). os.getenv alone often misses LOGO_URL.
    logo_url = (email_settings.logo_url or os.getenv("LOGO_URL") or "").strip()
    if logo_url.startswith(("http://", "https://")):
        return logo_url
    return _DEFAULT_LOGO_URL


def _render_email_layout(title: str, body_html: str, hero_text: str | None = None) -> str:
    return _render_template(
        "layouts/base_email.html",
        {
            "title": title,
            "hero_text": hero_text or title,
            "body_html": body_html,
            "logo_url": _resolve_logo_url(),
        },
        raw_keys={"body_html", "logo_url"},
    )


def _paragraphs(text: str) -> str:
    return "".join(f'<p style="margin:0 0 14px;">{escape(part)}</p>' for part in text.splitlines() if part.strip())


_VERIFICATION_HERO_TEXT = (
    "Our mission is to connect and empower university students to achieve their educational goals."
)


def _otp_template_details(otp_purpose: str) -> tuple[str, str, str, str]:
    """Return (subject_title, grey_header, grey_body, hero_text)."""
    match otp_purpose:
        case "email_verification":
            return (
                "Email Verification",
                "Email Verification",
                "Use this one-time password (OTP) to verify your email address.",
                _VERIFICATION_HERO_TEXT,
            )
        case "password_reset":
            return (
                "Temporary Password",
                "Your Temporary Password",
                "Use this as one-time password to set your password",
                "Temporary Password",
            )
        case _:
            return (
                "Email Verification",
                "Email Verification",
                "Use this one-time password (OTP) to verify your email address.",
                _VERIFICATION_HERO_TEXT,
            )


def _build_otp_display_html(otp: str, brand_blue: str) -> str:
    """Build the OTP display as a full-width dashed card with large spaced digits.
    Works for any OTP length; digits are space-separated for clarity.
    """
    spaced = " ".join(escape(ch) for ch in otp)
    return (
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin: 8px 0 4px;">'
        "<tr>"
        f'<td align="center" style="padding: 28px 20px; background-color: #FFFFFF; border: 2px dashed {brand_blue}; border-radius: 8px;">'
        f'<div style="font-size: 11px; font-weight: 600; color: {brand_blue}; letter-spacing: 1.5px; text-transform: uppercase; font-family: Arial, Helvetica, sans-serif; margin-bottom: 10px;">Your OTP</div>'
        f'<span class="otp-font" style="font-size: 36px; font-weight: 700; color: #071A35; letter-spacing: 12px; font-family: \'Courier New\', Courier, monospace; display: inline-block; padding-left: 12px;">{spaced}</span>'
        "</td>"
        "</tr>"
        "</table>"
    )


def build_otp_email_html(otp: str, otp_purpose: str = "email_verification") -> str:
    title, header, body_text, hero_text = _otp_template_details(otp_purpose)
    otp_expire_minutes = email_settings.otp_expire_minutes

    brand_blue = str(BRAND_COLORS.get("brand_blue", "#0B5FA5"))
    otp_display_html = _build_otp_display_html(otp, brand_blue)

    raw_keys = {"otp_display"}
    body_html = _render_template(
        "auth/otp_email.html",
        {
            "otp": otp,
            "header": header,
            "body_text": body_text,
            "otp_display": otp_display_html,
            "otp_expire_minutes": otp_expire_minutes,
        },
        raw_keys=raw_keys,
    )
    return _render_email_layout(title, body_html, hero_text=hero_text)


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
    base_url = email_settings.base_url.rstrip("/")
    body_html = _render_template("auth/account_created_email.html", {"greeting": greeting, "base_url": base_url})
    return _render_email_layout("Account Created Successfully", body_html)


def build_temporary_password_email_html(
    temporary_password: str,
    full_name: str | None = None,
    role: str | None = None,
) -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    role_text = f" as a {role}" if role else ""
    body_html = (
        '<div style="font-size:16px;font-weight:700;margin-bottom:16px;">Welcome to KampuLynk</div>'
        f'<p style="margin:0 0 14px;">{escape(greeting)}</p>'
        f'<p style="margin:0 0 14px;">Your KampuLynk account{escape(role_text)} has been created by an administrator.</p>'
        '<p style="margin:0 0 10px;">Use this temporary password to sign in:</p>'
        '<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="margin:8px 0 16px;">'
        '<tr><td align="center" style="padding:18px 16px;background-color:#F8FAFC;border:1px dashed #CBD5E1;">'
        f'<span style="font-size:20px;font-weight:700;color:#071A35;font-family:\'Courier New\', Courier, monospace;">{escape(temporary_password)}</span>'
        '</td></tr></table>'
        '<p style="margin:0;">Please change this password after your first sign-in.</p>'
    )
    return _render_email_layout("KampuLynk Account Created", body_html)


def build_password_changed_email_html(full_name: str | None = None) -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"


def build_password_changed_email_html(full_name: str | None = None) -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    base_url = email_settings.base_url.rstrip("/")
    body_html = _render_template("auth/password_changed_email.html", {"greeting": greeting, "base_url": base_url})
    return _render_email_layout("Password Changed Successfully", body_html)


async def send_otp_email(
    to_email: str,
    otp: str,
    otp_purpose: str = "password_reset",
    background_tasks: BackgroundTasks | None = None,
) -> bool:
    """Send OTP in the background. Users are actively waiting for this."""
    title, _, _, _ = _otp_template_details(otp_purpose)
    purpose = f"OTP: {otp_purpose}"
    subject = f"KampuLynk {title}"
    html_content = build_otp_email_html(otp, otp_purpose)

    send_email_in_background(background_tasks, to_email, subject, html_content, purpose)
    return True


async def send_account_created_email(to_email: str, full_name: str | None = None) -> bool:
    return await _send_email(to_email, "KampuLynk Account Created", build_account_created_email_html(full_name), purpose="Account Created")


def build_lynkup_response_email_html(full_name: str | None = None, response_status: str = "accepted") -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    base_url = email_settings.base_url.rstrip("/")
    # Capitalize first letter for display
    display_status = response_status.capitalize()
    body_html = _render_template(
        "connections/lynkup_response_email.html", 
        {"greeting": greeting, "base_url": base_url, "response_status": response_status, "response_status_title": display_status}
    )
    # Replaces `response_status|title` manually because _render_template might not support Jinja pipes
    body_html = body_html.replace("{{response_status|title}}", display_status)
    return _render_email_layout(f"Connection Request {display_status}", body_html)


async def send_lynkup_response_email(to_email: str, response_status: str, full_name: str | None = None) -> bool:
    """Queue Lynkup response email for cron delivery."""
    subject = f"KampuLynk Connection {response_status.capitalize()}"
    html_content = build_lynkup_response_email_html(full_name, response_status)
    purpose = f"Connection{response_status.capitalize()}"
    return await _queue_email(to_email, subject, html_content, purpose=purpose)


_NEGATIVE_REVIEW_STATUSES = ("flag", "flagged", "reject", "rejected")


def build_post_review_email_html(
    full_name: str | None = None,
    review_status: str = "published",
) -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    if review_status in _NEGATIVE_REVIEW_STATUSES:
        title = "Please Review Your Post"
        body = "A moderator has flagged your post. Please review your post and make the necessary updates before submitting it again."
    else:
        title = "Your Post Has Been Published"
        body = "Good news! A moderator has approved your post and it has been published."

    body_html = (
        f'<div style="font-size:16px;font-weight:700;margin-bottom:16px;">{escape(title)}</div>'
        f'<p style="margin:0 0 14px;">{escape(greeting)}</p>'
        f'<p style="margin:0;">{escape(body)}</p>'
    )
    return _render_email_layout(title, body_html)


async def send_post_review_email(
    to_email: str,
    review_status: str,
    full_name: str | None = None,
) -> bool:
    """Queue post review result email for cron delivery."""
    if review_status in _NEGATIVE_REVIEW_STATUSES:
        subject = "KampuLynk Post Flagged"
        purpose = "Post Flagged"
    else:
        subject = "KampuLynk Post Published"
        purpose = "Post Published"
    html_content = build_post_review_email_html(full_name, review_status)
    return await _queue_email(to_email, subject, html_content, purpose=purpose)


async def send_temporary_password_email(
    to_email: str,
    temporary_password: str,
    full_name: str | None = None,
    role: str | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> bool:
    subject = "KampuLynk Account Created"
    html_content = build_temporary_password_email_html(temporary_password, full_name, role)
    send_email_in_background(
        background_tasks,
        to_email,
        subject,
        html_content,
        purpose="Account Created",
    )
    return True


async def send_password_changed_email(to_email: str, full_name: str | None = None) -> bool:
    return await _send_email(to_email, "KampuLynk Password Changed", build_password_changed_email_html(full_name), purpose="Password Changed")


# New function to send email verification success notification
async def send_verification_success_email(
    to_email: str,
    full_name: str | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> bool:
    """Send email-verified confirmation in the background.
    Uses the HTML template built by `build_email_verified_success_html`.
    """
    subject = "KampuLynk Email Verified"
    html_content = build_email_verified_success_html(full_name)
    send_email_in_background(background_tasks, to_email, subject, html_content, purpose="Email Verified")
    return True


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


async def send_reset_password_email(
    to_email: str,
    reset_link: str,
    background_tasks: BackgroundTasks | None = None,
) -> bool:
    """Send password reset link. Delivers immediately when no BackgroundTasks is provided."""
    subject = "Reset Your Password"
    password_reset_expire_minutes = email_settings.password_reset_token_expire_minutes
    body_html = _render_template(
        "auth/password_reset_email.html",
        {"reset_link": reset_link, "subject": subject, "password_reset_expire_minutes": password_reset_expire_minutes},
        raw_keys={"reset_link"},
    )
    html_content = _render_email_layout(subject, body_html)
    purpose = "forget password"

    if background_tasks:
        send_email_in_background(background_tasks, to_email, subject, html_content, purpose)
        logger.info("Password reset email queued via BackgroundTasks for %s", to_email)
        return True

    logger.info("Password reset email sending immediately to %s", to_email)
    return await _send_and_log_email(to_email, subject, html_content, purpose)


def build_profile_updated_email_html(full_name: str | None = None) -> str:
    greeting = f"Hi {full_name}," if full_name else "Hi,"
    body_html = (
        f'<div style="font-size:16px;font-weight:700;margin-bottom:16px;">Profile Updated Successfully</div>'
        f'<p style="margin:0 0 14px;">{greeting}</p>'
        f'<p style="margin:0 0 14px;">Your KampuLynk profile has been successfully updated.</p>'
        f'<p style="margin:0;">If you did not perform this change, please contact support immediately.</p>'
    )
    return _render_email_layout("Profile Updated", body_html)


async def send_profile_updated_email(to_email: str, full_name: str | None = None) -> bool:
    """Queue Profile Updated email for cron delivery."""
    subject = "KampuLynk Profile Updated"
    html_content = build_profile_updated_email_html(full_name)
    purpose = "Profile Updated"
    return await _queue_email(to_email, subject, html_content, purpose=purpose)
