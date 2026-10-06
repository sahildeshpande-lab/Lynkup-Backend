from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import BackgroundTasks

from apps.administration.db_models.template_db_model import Template
from core.email.config import _PLACEHOLDER_FROM_EMAIL, settings as email_settings

logger = logging.getLogger(__name__)

_DEFAULT_LOGO_URL = "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/logo/logo.png"
_LOGO_CID = "kampulynk-logo"
_LOGO_FILE = Path(__file__).resolve().parents[1] / "static" / "email" / "kampulynk-logo.png"


# Load template services on demand: their package imports account services,
# which in turn import this module during worker startup.
def render_template(*args, **kwargs):
    from apps.administration.services.template_service import render_template as render

    return render(*args, **kwargs)


async def render_email_by_name(*args, **kwargs):
    from apps.administration.services.template_service import render_email_by_name as render

    return await render(*args, **kwargs)


def _resolve_logo_url(*, prefer_cid: bool = True) -> str:
    from apps.administration.services.template_service import resolve_logo_url

    return resolve_logo_url(prefer_cid=prefer_cid)


def _sender_is_placeholder(from_email: str) -> bool:
    return from_email.strip().lower() == _PLACEHOLDER_FROM_EMAIL


async def _should_send_to_user(to_email: str) -> bool:
    return True


def _from_email() -> str:
    return email_settings.sendgrid_from_email


def _from_name() -> str:
    return (email_settings.sendgrid_from_name or "").strip() or "KampuLynk"


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
) -> None:
    """Create one transactional email log entry."""
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
                attachment=attachment,
                updated_at=datetime.now(timezone.utc),
            )
            session.add(log_entry)
            await session.commit()
    except Exception as e:
        logger.exception("Failed to log transactional email: %s", e)


def _attach_bytes_to_message(
    message,
    *,
    content: bytes,
    file_name: str,
    content_type: str | None = None,
) -> None:
    """Attach raw bytes to a SendGrid Mail message."""
    try:
        import base64
        from sendgrid.helpers.mail import (
            Attachment,
            Disposition,
            FileContent,
            FileName,
            FileType,
        )
    except Exception:
        logger.exception("Could not import SendGrid attachment helpers for bytes")
        return

    encoded = base64.b64encode(content).decode("ascii")
    attachment = Attachment(
        FileContent(encoded),
        FileName(file_name),
        FileType(content_type or "application/octet-stream"),
        Disposition("attachment"),
    )
    message.add_attachment(attachment)
    logger.info("Attached file %s (%d bytes) to email", file_name, len(content))


def _attach_file_from_path(message, attachment_path: str | None) -> None:
    """Attach a local file to a SendGrid Mail message when the path exists."""
    if not attachment_path:
        return
    path = Path(attachment_path)
    if not path.is_file():
        logger.warning("Email attachment missing on disk: %s", attachment_path)
        return
    import mimetypes

    mime_type, _ = mimetypes.guess_type(str(path))
    _attach_bytes_to_message(
        message,
        content=path.read_bytes(),
        file_name=path.name,
        content_type=mime_type or "application/octet-stream",
    )


def _attach_bytes_payloads(message, attachments: list[dict] | None) -> None:
    for item in attachments or []:
        content = item.get("content")
        if not isinstance(content, (bytes, bytearray)):
            continue
        _attach_bytes_to_message(
            message,
            content=bytes(content),
            file_name=str(item.get("file_name") or "attachment.bin"),
            content_type=str(item.get("content_type") or "application/octet-stream"),
        )


def _extract_sendgrid_message_id(response) -> str | None:
    headers = getattr(response, "headers", None) or {}
    if hasattr(headers, "get"):
        return headers.get("X-Message-Id") or headers.get("X-Message-ID")
    try:
        return headers["X-Message-Id"]
    except Exception:
        return None


def _attach_inline_logo(message) -> None:
    """Attach bundled logo as inline CID image when HTML references it."""
    if not _LOGO_FILE.is_file():
        return
    try:
        import base64
        from sendgrid.helpers.mail import (
            Attachment,
            ContentId,
            Disposition,
            FileContent,
            FileName,
            FileType,
        )
    except Exception:
        logger.exception("Could not import SendGrid attachment helpers for logo")
        return

    encoded = base64.b64encode(_LOGO_FILE.read_bytes()).decode("ascii")
    attachment = Attachment(
        FileContent(encoded),
        FileName("kampulynk-logo.png"),
        FileType("image/png"),
        Disposition("inline"),
        ContentId(_LOGO_CID),
    )
    message.add_attachment(attachment)


_NO_CLICK_TRACKING_PURPOSES = frozenset({"Data Export Ready"})


def _disable_sendgrid_click_tracking(message) -> None:
    """Leave hrefs unchanged so signed Spaces ZIP URLs stay HTTPS."""
    try:
        from sendgrid.helpers.mail import ClickTracking, TrackingSettings
    except Exception:
        logger.exception("Could not import SendGrid tracking helpers")
        return
    tracking = TrackingSettings()
    tracking.click_tracking = ClickTracking(False, False)
    message.tracking_settings = tracking


async def _deliver_email_via_sendgrid(
    to_email: str,
    subject: str,
    html_body: str,
    from_email: str,
    attachment_path: str | None = None,
    *,
    plain_text: str | None = None,
    attachments: list[dict] | None = None,
    disable_click_tracking: bool = False,
) -> tuple[bool, str | None, str | None]:
    api_key = email_settings.sendgrid_api_key
    if not api_key or not from_email or _sender_is_placeholder(from_email or ""):
        logger.warning("Email send simulated: SendGrid is not fully configured for %s", to_email)
        return True, None, None

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Email, Mail
    except Exception:
        message = "SendGrid client could not be imported"
        logger.exception("Email send skipped: %s", message)
        return False, message, None

    try:
        mail_kwargs = {
            "from_email": Email(from_email, _from_name()),
            "to_emails": to_email,
            "subject": subject,
            "html_content": html_body,
        }
        if plain_text:
            mail_kwargs["plain_text_content"] = plain_text
        message = Mail(**mail_kwargs)
        if disable_click_tracking:
            _disable_sendgrid_click_tracking(message)
        if f"cid:{_LOGO_CID}" in (html_body or ""):
            _attach_inline_logo(message)
        _attach_file_from_path(message, attachment_path)
        _attach_bytes_payloads(message, attachments)
        client = SendGridAPIClient(api_key)
        response = client.send(message)
        success = 200 <= response.status_code < 300
        if not success:
            message = f"SendGrid rejected email with status {getattr(response, 'status_code', None)}"
            logger.error("%s for %s", message, to_email)
            return False, message, None
        return True, None, _extract_sendgrid_message_id(response)
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
            return True, None, None
        body = getattr(exc, "body", None)
        if body:
            message = f"{message}: {body}"
        logger.exception(
            "Email send failed while delivering to %s from %s",
            to_email,
            from_email,
        )
        return False, message, None


async def _actually_send_email_via_sendgrid(
    to_email: str,
    subject: str,
    html_body: str,
    from_email: str,
    attachment_path: str | None = None,
    *,
    disable_click_tracking: bool = False,
) -> bool:
    success, _, _ = await _deliver_email_via_sendgrid(
        to_email,
        subject,
        html_body,
        from_email,
        attachment_path=attachment_path,
        disable_click_tracking=disable_click_tracking,
    )
    return success


async def send_bulk_campaign_email(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    plain_text: str | None = None,
    attachments: list[dict] | None = None,
):
    """Send one bulk-campaign email (single recipient). Prefer ``send_bulk_campaign_batch``."""
    from apps.bulk_send.schemas import DeliveryResult

    success, error_message, message_id = await _deliver_email_via_sendgrid(
        to_email,
        subject,
        html_body,
        _from_email(),
        plain_text=plain_text,
        attachments=attachments,
    )
    return DeliveryResult(
        success=success,
        sendgrid_message_id=message_id,
        failure_reason=None if success else (error_message or "Failed to send email"),
        retryable=not success,
    )


async def _deliver_bulk_batch_via_sendgrid(
    *,
    subject: str,
    html_body: str,
    from_email: str,
    recipients: list[dict],
    plain_text: str | None = None,
    attachments: list[dict] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Send one Mail Send request with one personalization per recipient."""
    from apps.bulk_send.enums import SENDGRID_MAX_PERSONALIZATIONS

    if not recipients:
        return True, None, None
    if len(recipients) > SENDGRID_MAX_PERSONALIZATIONS:
        return (
            False,
            f"Batch exceeds SendGrid personalization limit of {SENDGRID_MAX_PERSONALIZATIONS}",
            None,
        )

    api_key = email_settings.sendgrid_api_key
    if not api_key or not from_email or _sender_is_placeholder(from_email or ""):
        logger.warning(
            "Bulk email send simulated: SendGrid is not fully configured (recipients=%d)",
            len(recipients),
        )
        return True, None, None

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import (
            Content,
            CustomArg,
            Email,
            Mail,
            Personalization,
            To,
        )
    except Exception:
        message = "SendGrid client could not be imported"
        logger.exception("Bulk email send skipped: %s", message)
        return False, message, None

    try:
        message = Mail()
        message.from_email = Email(from_email, _from_name())
        message.subject = subject
        if plain_text:
            message.add_content(Content("text/plain", plain_text))
        message.add_content(Content("text/html", html_body or ""))

        for recipient in recipients:
            personalization = Personalization()
            personalization.add_to(To(str(recipient["email"])))
            personalization.add_custom_arg(
                CustomArg("campaign_id", str(recipient["campaign_id"]))
            )
            personalization.add_custom_arg(
                CustomArg("delivery_id", str(recipient["delivery_id"]))
            )
            message.add_personalization(personalization)

        if f"cid:{_LOGO_CID}" in (html_body or ""):
            _attach_inline_logo(message)
        _attach_bytes_payloads(message, attachments)

        client = SendGridAPIClient(api_key)
        response = client.send(message)
        success = 200 <= response.status_code < 300
        if not success:
            err = f"SendGrid rejected bulk email with status {getattr(response, 'status_code', None)}"
            logger.error("%s for %d recipients", err, len(recipients))
            return False, err, None
        return True, None, _extract_sendgrid_message_id(response)
    except Exception as exc:
        err = str(exc)
        exc_name = type(exc).__name__
        if "UnauthorizedError" in exc_name or "401" in err or "Unauthorized" in err:
            logger.warning(
                "SendGrid API Key unauthorized (401). Simulating bulk delivery for %d recipients.",
                len(recipients),
            )
            return True, None, None
        logger.exception("Bulk email send failed for %d recipients", len(recipients))
        return False, err, None


async def send_bulk_campaign_batch(
    *,
    subject: str,
    html_body: str,
    recipients: list[dict],
    plain_text: str | None = None,
    attachments: list[dict] | None = None,
):
    """Send one SendGrid request covering many personalizations."""
    from apps.bulk_send.schemas import BulkBatchSendResult

    success, error_message, message_id = await _deliver_bulk_batch_via_sendgrid(
        subject=subject,
        html_body=html_body,
        from_email=_from_email(),
        recipients=recipients,
        plain_text=plain_text,
        attachments=attachments,
    )
    return BulkBatchSendResult(
        success=success,
        sendgrid_message_id=message_id,
        failure_reason=None if success else (error_message or "Failed to send email"),
        retryable=not success,
        recipient_count=len(recipients),
    )


async def _send_and_log_email(
    to_email: str,
    subject: str,
    html_body: str,
    purpose: str,
    from_email: str | None = None,
    log_id: UUID | None = None,
    attachment: str | None = None,
    session_factory=None,
    lease_owner: str | None = None,
) -> bool:
    """Core function to actually deliver an email and log/update its transaction status.

    If `log_id` is provided, it updates the existing log record (used by cron).
    Otherwise, it creates a new successful/failed log record (used by immediate).
    """
    from_email = from_email or _from_email()
    if not await _should_send_to_user(to_email):
        logger.info("Email send skipped: is_send flag is false for %s", to_email)
        return False

    success = await _actually_send_email_via_sendgrid(
        to_email,
        subject,
        html_body,
        from_email,
        attachment_path=attachment,
        disable_click_tracking=purpose in _NO_CLICK_TRACKING_PURPOSES,
    )
    error_message = None if success else "Failed to send email"
    factory = _resolve_session_factory(session_factory)

    try:
        from apps.accounts.db_models import TransactionalEmailLog
        from sqlmodel import select

        async with factory() as session:
            if log_id and lease_owner is not None:
                updated = await _complete_transactional_email_if_owner(
                    session,
                    log_id=log_id,
                    lease_owner=lease_owner,
                    is_send=success,
                )
                if updated:
                    await session.commit()
                    if success:
                        logger.info("Email ID %s successfully sent.", log_id)
                    else:
                        logger.warning(
                            "Email ID %s failed to send: %s",
                            log_id,
                            error_message,
                        )
                else:
                    logger.info(
                        "Skipped updating email ID %s; lease is no longer owned",
                        log_id,
                    )
            elif log_id:
                # Update existing log
                stmt = select(TransactionalEmailLog).where(TransactionalEmailLog.id == log_id).with_for_update()
                db_log = (await session.execute(stmt)).scalars().first()
                if db_log:
                    db_log.is_send = success
                    db_log.updated_at = datetime.now(timezone.utc)
                    session.add(db_log)
                    await session.commit()
                    if success:
                        logger.info("Email ID %s successfully sent.", log_id)
                    else:
                        logger.warning(
                            "Email ID %s failed to send: %s",
                            log_id,
                            error_message,
                        )
            else:
                # Create a new log entry
                log_entry = TransactionalEmailLog(
                    to_email=to_email,
                    from_email=from_email,
                    content=html_body,
                    purpose=purpose,
                    subject=subject,
                    is_send=success,
                    attachment=attachment,
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(log_entry)
                await session.commit()
                if success:
                    logger.info("Immediate email successfully sent and logged")
                else:
                    logger.error("Immediate email failed to send and logged: %s", error_message)
    except Exception as e:
        logger.exception("Failed to write/update transactional email log: %s", e)

    return success


async def send_email_in_background(
    background_tasks: BackgroundTasks | None,
    to_email: str,
    subject: str,
    html_body: str,
    purpose: str,
    from_email: str | None = None,
) -> bool:
    """Persist delivery before returning; Celery owns all sending.

    background_tasks is retained for compatibility with existing callers.
    """
    return await _queue_email(
        to_email, subject, html_body, purpose, from_email=from_email
    )


async def _send_email(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None) -> bool:
    if not await _should_send_to_user(to_email):
        logger.info("Email send skipped: is_send flag is false for %s", to_email)
        return False

    await _queue_email(to_email, subject, html_body, purpose, attachment)
    logger.info("Email queued for %s", to_email)
    return True


CONNECTION_REMINDER_PURPOSE = "Connection Reminder"
GRADUATION_COMPLETION_PURPOSE = "Graduation Completion"


async def _queue_email(
    to_email: str, subject: str, html_body: str, purpose: str,
    attachment: str | None = None, *, from_email: str | None = None,
) -> bool:
    """Commit a durable pending email, then request prompt Celery delivery."""
    from apps.accounts.db_models import TransactionalEmailLog
    from core.database.session import async_session_factory
    from core.jobs.publishing import publish_task
    from core.celery_worker.config import CeleryTaskQueue

    if not await _should_send_to_user(to_email):
        return False
    async with async_session_factory() as session:
        session.add(TransactionalEmailLog(
            to_email=to_email, from_email=from_email or _from_email(),
            subject=subject, content=html_body, purpose=purpose,
            attachment=attachment, is_send=False,
        ))
        await session.commit()
    try:
        await publish_task(
            "kampulynk.email.transactional.tick", CeleryTaskQueue.TRANSACTIONAL_QUEUE
        )
    except Exception:
        # Beat and reconciliation recover the committed row during broker outages.
        logger.exception("Email persisted; immediate Celery publication failed")
    return True


async def queue_email_on_session(
    session,
    to_email: str,
    subject: str,
    html_body: str,
    purpose: str,
    attachment: str | None = None,
) -> bool:
    """Queue a transactional email on an existing DB session (no commit)."""
    if not await _should_send_to_user(to_email):
        logger.info("Email queue skipped: is_send flag is false for %s", to_email)
        return False

    from apps.accounts.db_models import TransactionalEmailLog

    session.add(
        TransactionalEmailLog(
            to_email=to_email,
            from_email=_from_email(),
            content=html_body,
            purpose=purpose,
            subject=subject,
            is_send=False,
            attachment=attachment,
            updated_at=datetime.now(timezone.utc),
        )
    )
    return True


async def _send_email_immediately(to_email: str, subject: str, html_body: str, purpose: str, attachment: str | None = None) -> bool:
    """Compatibility entry point: delivery now belongs to Celery."""
    return await _queue_email(to_email, subject, html_body, purpose, attachment)


def _resolve_session_factory(session_factory=None):
    if session_factory is not None:
        return session_factory
    from core.database.session import async_session_factory

    return async_session_factory

async def _complete_transactional_email_if_owner(
    session,
    *,
    log_id: UUID,
    lease_owner: str,
    is_send: bool,
) -> bool:
    """Persist delivery outcome only while this worker still owns the lease."""
    from sqlalchemy import update

    from apps.accounts.db_models import TransactionalEmailLog

    result = await session.execute(
        update(TransactionalEmailLog)
        .where(TransactionalEmailLog.id == log_id)
        .where(TransactionalEmailLog.lease_owner == lease_owner)
        .values(
            is_send=is_send,
            updated_at=datetime.now(timezone.utc),
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1

async def run_transactional_email_tick(
    *,
    session_factory=None,
    lease_owner: str | None = None,
    limit: int = 10,
) -> int:
    """Run producers then pending transactional emails.

    Producer failures are logged and do not prevent delivery.
    Unexpected processing failures propagate to the caller.
    """
    logger.info("[transactional-email] Tick started")
    try:
        from apps.connections.services.connection_reminder_service import (
            process_connection_reminders,
        )

        await process_connection_reminders(session_factory=session_factory)
    except Exception:
        logger.exception("Connection reminder producer failed")
    try:
        from apps.profiles.services.graduation_email_service import (
            process_graduation_completion_emails,
        )

        await process_graduation_completion_emails(session_factory=session_factory)
    except Exception:
        logger.exception("Graduation email producer failed")
    return await process_pending_emails(
        limit=limit,
        session_factory=session_factory,
        lease_owner=lease_owner,
    )


async def process_pending_emails(
    limit: int = 10,
    *,
    session_factory=None,
    lease_owner: str | None = None,
    purpose: str | None = None,
) -> int:
    from sqlalchemy import func, or_, update
    from sqlmodel import select

    from apps.accounts.db_models import TransactionalEmailLog
    from core.jobs.claims import claim_transactional_email

    factory = _resolve_session_factory(session_factory)
    owner = lease_owner or f"transactional-email:{uuid4()}"
    purpose_note = f" purpose={purpose!r}" if purpose else ""
    logger.info(
        "Cron started: processing pending emails (limit: %d)%s...",
        limit,
        purpose_note,
    )

    async with factory() as session:
        stmt = (
            select(TransactionalEmailLog)
            .where(TransactionalEmailLog.is_send == False)
            .where(
                or_(
                    TransactionalEmailLog.lease_owner.is_(None),
                    TransactionalEmailLog.lease_expires_at.is_(None),
                    TransactionalEmailLog.lease_expires_at < func.now(),
                )
            )
            .order_by(TransactionalEmailLog.created_at.asc())
            .limit(limit)
        )
        if purpose is not None:
            stmt = stmt.where(TransactionalEmailLog.purpose == purpose)
        pending_emails = (await session.execute(stmt)).scalars().all()
        snapshots = [
            (
                email.id,
                email.to_email,
                email.subject,
                email.content,
                email.purpose,
                email.attachment,
            )
            for email in pending_emails
        ]

    if not snapshots:
        logger.info("Cron finished: no pending transactional emails to process.")
        return 0

    email_ids = [email_id for email_id, *_ in snapshots]
    logger.info("Cron processing %d emails: %s", len(email_ids), email_ids)

    processed = 0
    success_count = 0
    failure_count = 0

    for email_id, to_email, subject, html_body, purpose, attachment in snapshots:
        async with factory() as session:
            claimed = await claim_transactional_email(
                session,
                email_id=email_id,
                lease_owner=owner,
            )
        if not claimed.claimed:
            logger.info("Email ID %s could not be claimed; skipping", email_id)
            continue

        try:
            logger.info("Processing queued email ID %s", email_id)
            success = await _send_and_log_email(
                to_email=to_email,
                subject=subject,
                html_body=html_body,
                purpose=purpose,
                from_email=_from_email(),
                log_id=email_id,
                attachment=attachment,
                session_factory=factory,
                lease_owner=owner,
            )
            processed += 1
            if success:
                success_count += 1
            else:
                failure_count += 1
        except Exception as e:
            failure_count += 1
            logger.exception("Unexpected failure processing email ID %s: %s", email_id, e)
            try:
                async with factory() as fail_session:
                    result = await fail_session.execute(
                        update(TransactionalEmailLog)
                        .where(TransactionalEmailLog.id == email_id)
                        .where(TransactionalEmailLog.lease_owner == owner)
                        .values(updated_at=datetime.now(timezone.utc))
                        .execution_options(synchronize_session=False)
                    )
                    if result.rowcount == 1:
                        await fail_session.commit()
            except Exception as db_exc:
                logger.error(
                    "Failed to log unexpected error for email ID %s to database: %s",
                    email_id,
                    db_exc,
                )

    logger.info(
        "Cron finished: processed %d emails. Successes: %d, Failures: %d.",
        processed,
        success_count,
        failure_count,
    )
    return processed


async def process_pending_bulk_emails(limit: int | None = None) -> int:
    from apps.bulk_send.delivery_service import process_pending_bulk_deliveries
    from apps.bulk_send.enums import SENDGRID_MAX_PERSONALIZATIONS

    batch_limit = SENDGRID_MAX_PERSONALIZATIONS if limit is None else limit
    return await process_pending_bulk_deliveries(limit=batch_limit)


async def process_transactional_emails() -> None:
    """One scheduler tick: producers, then pending transactional emails.

    Producer failures are logged and do not prevent delivery.
    Bulk campaign deliveries are handled by ``apps.bulk_send.cron.process_bulk_emails``.
    """
    try:
        await run_transactional_email_tick()
    except Exception as e:
        logger.exception("Failed running transactional email cron task: %s", e)


async def cron_send_emails() -> None:
    await process_transactional_emails()


def _email_greeting(first_name: str | None = None) -> str:
    normalized = (first_name or "").strip()
    return f"Hello {normalized}," if normalized else "Hello User,"


def _paragraphs(text: str) -> str:
    return "".join(
        f'<p style="margin:0 0 14px;text-align:center;">{escape(part)}</p>'
        for part in text.splitlines()
        if part.strip()
    )


def _html_to_plain_text(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def _bulk_email_body_html(body_html: str) -> str:
    cleaned = (body_html or "").strip()
    if not cleaned:
        return ""
    if re.search(r"<[a-zA-Z/]", cleaned):
        return (
            '<div style="text-align:left;font:14px Arial,Helvetica,sans-serif;'
            f'color:#333;line-height:1.7;">{cleaned}</div>'
        )
    return (
        '<div style="text-align:left;">'
        f"{_paragraphs(cleaned)}"
        "</div>"
    )


def build_bulk_email_plain_text(
    name: str,
    subject: str,
    body_text: str | None = None,
    body_html: str | None = None,
) -> str:
    _ = name, subject
    if body_text and body_text.strip():
        return body_text.strip()
    if body_html and body_html.strip():
        return _html_to_plain_text(body_html)
    return ""


def _otp_template_details(otp_purpose: str) -> tuple[str, str]:
    match otp_purpose:
        case "password_reset":
            return ("Temporary Password", "Please use this one-time temporary password to securely access your account.")
        case "email_verification" | _:
            return ("Email Verification", "Please use this one-time code to verify your email address and securely access your account.")


def _build_otp_display_html(otp: str, brand_blue: str) -> str:
    digits = [escape(ch) for ch in str(otp or "")]
    digit_cells = "".join(
        (
            '<td class="otp-digit" align="center" '
            'style="padding:0 12px;font-size:32px;font-weight:700;color:#071A35;'
            "font-family:'Courier New',Courier,monospace;letter-spacing:6px;line-height:1;\""
            f">{digit}</td>"
        )
        for digit in digits
    )

    return (
        '<table class="otp-outer-table" role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'align="center" style="margin:4px auto 14px;border-collapse:collapse;width:100%;max-width:340px;">'
        "<tr>"
        f'<td class="otp-card" align="center" '
        f'style="padding:16px 20px;background-color:#F8FAFC;border:2px dashed {brand_blue};'
        'border-radius:8px;text-align:center;box-sizing:border-box;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'align="center" style="margin:0 auto;border-collapse:collapse;">'
        "<tr>"
        f"{digit_cells}"
        "</tr>"
        "</table>"
        "</td>"
        "</tr>"
        "</table>"
    )


def _notification_template_db_name(notification_type: str, template_name: str | None = None) -> str:
    if template_name:
        clean = template_name.removesuffix(".html")
        return clean
    match notification_type:
        case "topic":
            return "notification_topic_email"
        case "broadcast":
            return "notification_broadcast_email"
        case "configuration":
            return "notification_configuration_email"
        case "resend-otp":
            return "notification_resend_otp_email"
        case "user-creation":
            return "notification_user_creation_email"
        case _:
            return "notification_send_email"


def _graduation_university_name(university_name: str | None) -> str:
    cleaned = (university_name or "").strip()
    return cleaned or "your university"


_NEGATIVE_REVIEW_STATUSES = ("flag", "flagged", "reject", "rejected")


# Backward-compatible synchronous builder methods using default template definitions
def _get_seeded_template(name: str) -> Template:
    from apps.administration.initial_templates import INITIAL_TEMPLATES
    tpl_data = next((t for t in INITIAL_TEMPLATES if t["name"] == name), None)
    if not tpl_data:
        raise ValueError(f"Unknown template {name}")
    return Template(
        name=tpl_data["name"],
        subject=tpl_data["subject"],
        body_html=tpl_data["body_html"],
        status="active",
    )



def build_bulk_email_html(
    name: str,
    subject: str,
    body_html: str = "",
) -> str:
    tpl = _get_seeded_template("bulk_campaign_email")
    _, rendered = render_template(
        tpl,
        {
            "name": name,
            "subject": subject,
            "campaign_body_html": _bulk_email_body_html(body_html),
        },
        raw_keys={"campaign_body_html"},
    )
    return rendered


def build_otp_email_html(
    otp: str,
    otp_purpose: str = "email_verification",
    first_name: str | None = None,
) -> str:
    tpl = _get_seeded_template("otp_email")
    from apps.administration.services.template_service import BRAND_COLORS

    brand_blue = str(BRAND_COLORS.get("brand_blue", "#0B5FA5"))
    otp_display_html = _build_otp_display_html(otp, brand_blue)
    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "otp": otp,
            "displayotp": otp_display_html,
            "otp_minutes": email_settings.otp_expire_minutes,
        },
        raw_keys={"displayotp"},
    )
    return rendered


def build_notification_email_html(
    title: str,
    body: str,
    html_body: str | None = None,
    notification_type: str = "send",
    template_name: str | None = None,
) -> str:
    db_tpl_name = _notification_template_db_name(notification_type, template_name)
    tpl = _get_seeded_template(db_tpl_name)
    notification_body = html_body if html_body else _paragraphs(body)
    _, rendered = render_template(
        tpl,
        {
            "subject": title,
            "title": title,
            "notification_body": notification_body,
        },
        raw_keys={"notification_body"},
    )
    return rendered


def build_account_created_email_html(first_name: str | None = None) -> str:
    tpl = _get_seeded_template("account_created_email")
    greeting = _email_greeting(first_name)
    base_url = email_settings.base_url.rstrip("/")
    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "greeting": greeting,
            "base_url": base_url,
        },
    )
    return rendered


def build_temporary_password_email_html(
    temporary_password: str,
    first_name: str | None = None,
    role: str | None = None,
) -> str:
    tpl = _get_seeded_template("temporary_password_email")
    greeting = _email_greeting(first_name)
    role_text = f" as a {role}" if role else ""
    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "greeting": greeting,
            "role_text": role_text,
            "temporary_password": temporary_password,
        },
    )
    return rendered


def build_password_changed_email_html(first_name: str | None = None) -> str:
    tpl = _get_seeded_template("password_changed_email")
    greeting = _email_greeting(first_name)
    base_url = email_settings.base_url.rstrip("/")
    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "greeting": greeting,
            "base_url": base_url,
        },
    )
    return rendered


def build_email_verified_success_html(first_name: str | None = None) -> str:
    tpl = _get_seeded_template("email_verified_email")
    _, rendered = render_template(
        tpl,
        {
            "first_name": (first_name or "").strip() or "User",
        },
    )
    return rendered


def build_lynkup_response_email_html(
    first_name: str | None = None,
    response_status: str = "accepted",
    sender_profile_photo_url: str | None = None,
    sender_name: str | None = None,
    sender_major: str | None = None,
    sender_edu_level: str | None = None,
    sender_university: str | None = None,
) -> str:
    tpl = _get_seeded_template("lynkup_response_email")
    greeting = _email_greeting(first_name)
    display_status = response_status.capitalize()

    meta_parts = []
    university = (sender_university or "").strip()
    edu_level = (sender_edu_level or "").strip()
    if edu_level or university:
        meta_parts.append(f"{escape(university)} {escape(edu_level)}".strip())
    major_text = (sender_major or "").strip()
    if major_text:
        meta_parts.append(f"{escape(major_text)} Major")
    sender_meta_line = " | ".join(meta_parts)

    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "greeting": greeting,
            "response_status": response_status,
            "display_status": display_status,
            "response_status_title": display_status,
            "sender_profile_photo_url": sender_profile_photo_url or "",
            "sender_name": sender_name or "A KampuLynk User",
            "sender_meta_line": sender_meta_line,
            "base_url": email_settings.base_url.rstrip("/"),
        },
        raw_keys={"sender_profile_photo_url"},
    )
    return rendered


def build_connection_reminder_email_html(
    *,
    first_name: str | None,
    pending_count: int,
    request_cards_html: str,
    view_all_url: str,
) -> str:
    tpl = _get_seeded_template("connection_reminder_email")
    greeting = _email_greeting(first_name)
    if pending_count == 1:
        instruction = "You have 1 pending LynkUp request waiting for your response."
    else:
        instruction = f"You have {pending_count} pending LynkUp requests waiting for your response."

    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "greeting": greeting,
            "instruction": instruction,
            "pending_count": pending_count,
            "request_cards_html": request_cards_html,
            "view_all_url": view_all_url,
        },
        raw_keys={"request_cards_html", "view_all_url"},
    )
    return rendered


def build_post_review_email_html(
    first_name: str | None = None,
    review_status: str = "published",
) -> str:
    tpl = _get_seeded_template("post_review_email")
    greeting = _email_greeting(first_name)
    if review_status in _NEGATIVE_REVIEW_STATUSES:
        title = "Please Review Your Post"
        body = "A moderator has flagged your post. Please review your post and make the necessary updates before submitting it again."
        subject = "KampuLynk Post Flagged"
    else:
        title = "Your Post Has Been Published"
        body = "Good news! A moderator has approved your post and it has been published."
        subject = "KampuLynk Post Published"

    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "greeting": greeting,
            "title": title,
            "body": body,
            "subject": subject,
        },
    )
    return rendered


def build_password_reset_email_html(
    reset_link: str,
    first_name: str | None = None,
) -> str:
    tpl = _get_seeded_template("reset_password_email")
    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "reset_link": reset_link,
            "password_reset_expire_minutes": email_settings.password_reset_token_expire_minutes,
        },
        raw_keys={"reset_link"},
    )
    return rendered


def build_profile_updated_email_html(first_name: str | None = None) -> str:
    tpl = _get_seeded_template("profile_updated_email")
    greeting = _email_greeting(first_name)
    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "greeting": greeting,
        },
    )
    return rendered


def build_graduation_completion_email_html(
    first_name: str | None = None,
    university_name: str | None = None,
) -> str:
    tpl = _get_seeded_template("graduation_email")
    _, rendered = render_template(
        tpl,
        {
            "first_name": first_name or "User",
            "university_name": _graduation_university_name(university_name),
        },
    )
    return rendered


# Public Async Email Delivery Functions (Database-driven runtime resolution)

async def send_otp_email(
    to_email: str,
    otp: str,
    otp_purpose: str = "password_reset",
    background_tasks: BackgroundTasks | None = None,
    first_name: str | None = None,
) -> bool:
    """Send OTP in the background via database-driven template."""
    from apps.administration.services.template_service import BRAND_COLORS

    brand_blue = str(BRAND_COLORS.get("brand_blue", "#0B5FA5"))
    otp_display_html = _build_otp_display_html(otp, brand_blue)

    context = {
        "first_name": first_name or "User",
        "otp": otp,
        "displayotp": otp_display_html,
        "otp_minutes": email_settings.otp_expire_minutes,
    }
    raw_keys = {"displayotp"}
    subject, html_content = await render_email_by_name(None, "otp_email", context, raw_keys=raw_keys)
    purpose = f"OTP: {otp_purpose}"

    await send_email_in_background(background_tasks, to_email, subject, html_content, purpose)
    return True


async def send_account_created_email(to_email: str, first_name: str | None = None) -> bool:
    greeting = _email_greeting(first_name)
    base_url = email_settings.base_url.rstrip("/")
    context = {
        "first_name": first_name or "User",
        "greeting": greeting,
        "base_url": base_url,
    }
    subject, html_content = await render_email_by_name(None, "account_created_email", context)
    return await _send_email(to_email, subject, html_content, purpose="Account Created")


async def send_lynkup_response_email(
    to_email: str,
    response_status: str,
    first_name: str | None = None,
    sender_profile_photo_url: str | None = None,
    sender_name: str | None = None,
    sender_major: str | None = None,
    sender_edu_level: str | None = None,
    sender_university: str | None = None,
) -> bool:
    """Queue Lynkup response email for cron delivery."""
    greeting = _email_greeting(first_name)
    display_status = response_status.capitalize()

    meta_parts = []
    university = (sender_university or "").strip()
    edu_level = (sender_edu_level or "").strip()
    if edu_level or university:
        meta_parts.append(f"{escape(university)} {escape(edu_level)}".strip())
    major_text = (sender_major or "").strip()
    if major_text:
        meta_parts.append(f"{escape(major_text)} Major")
    sender_meta_line = " | ".join(meta_parts)

    context = {
        "first_name": first_name or "User",
        "greeting": greeting,
        "response_status": response_status,
        "display_status": display_status,
        "response_status_title": display_status,
        "sender_profile_photo_url": sender_profile_photo_url or "",
        "sender_name": sender_name or "A KampuLynk User",
        "sender_meta_line": sender_meta_line,
        "base_url": email_settings.base_url.rstrip("/"),
    }
    raw_keys = {"sender_profile_photo_url"}
    subject, html_content = await render_email_by_name(None, "lynkup_response_email", context, raw_keys=raw_keys)
    purpose = f"Connection{display_status}"
    return await _queue_email(to_email, subject, html_content, purpose=purpose)


async def send_post_review_email(
    to_email: str,
    review_status: str,
    first_name: str | None = None,
) -> bool:
    """Queue post review result email for cron delivery."""
    greeting = _email_greeting(first_name)
    if review_status in _NEGATIVE_REVIEW_STATUSES:
        title = "Please Review Your Post"
        body = "A moderator has flagged your post. Please review your post and make the necessary updates before submitting it again."
        default_subject = "KampuLynk Post Flagged"
        purpose = "Post Flagged"
    else:
        title = "Your Post Has Been Published"
        body = "Good news! A moderator has approved your post and it has been published."
        default_subject = "KampuLynk Post Published"
        purpose = "Post Published"

    context = {
        "first_name": first_name or "User",
        "greeting": greeting,
        "title": title,
        "body": body,
        "subject": default_subject,
    }
    subject, html_content = await render_email_by_name(None, "post_review_email", context)
    return await _queue_email(to_email, subject, html_content, purpose=purpose)


async def send_temporary_password_email(
    to_email: str,
    temporary_password: str,
    first_name: str | None = None,
    role: str | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> bool:
    greeting = _email_greeting(first_name)
    role_text = f" as a {role}" if role else ""
    context = {
        "first_name": first_name or "User",
        "greeting": greeting,
        "role_text": role_text,
        "temporary_password": temporary_password,
    }
    subject, html_content = await render_email_by_name(None, "temporary_password_email", context)
    await send_email_in_background(
        background_tasks,
        to_email,
        subject,
        html_content,
        purpose="Account Created",
    )
    return True


async def send_password_changed_email(to_email: str, first_name: str | None = None) -> bool:
    greeting = _email_greeting(first_name)
    base_url = email_settings.base_url.rstrip("/")
    context = {
        "first_name": first_name or "User",
        "greeting": greeting,
        "base_url": base_url,
    }
    subject, html_content = await render_email_by_name(None, "password_changed_email", context)
    return await _send_email(to_email, subject, html_content, purpose="Password Changed")


async def send_verification_success_email(
    to_email: str,
    first_name: str | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> bool:
    """Send email-verified confirmation in the background."""
    context = {
        "first_name": (first_name or "").strip() or "User",
    }
    subject, html_content = await render_email_by_name(None, "email_verified_email", context)
    await send_email_in_background(background_tasks, to_email, subject, html_content, purpose="Email Verified")
    return True


async def send_notification_email(
    to_email: str,
    subject: str,
    title: str,
    body: str,
    html_body: str | None = None,
    notification_type: str = "send",
    template_name: str | None = None,
) -> bool:
    template_key = _notification_template_db_name(notification_type, template_name)
    notification_body = html_body if html_body else _paragraphs(body)
    context = {
        "subject": subject,
        "title": title,
        "notification_body": notification_body,
    }
    raw_keys = {"notification_body"}
    _, html_content = await render_email_by_name(None, template_key, context, raw_keys=raw_keys)
    return await _send_email(
        to_email,
        subject,
        html_content,
        purpose=f"Notification: {notification_type}",
    )


async def send_reset_password_email(
    to_email: str,
    reset_link: str,
    first_name: str | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> bool:
    """Send password reset link."""
    context = {
        "first_name": first_name or "User",
        "reset_link": reset_link,
        "password_reset_expire_minutes": email_settings.password_reset_token_expire_minutes,
    }
    raw_keys = {"reset_link"}
    subject, html_content = await render_email_by_name(None, "reset_password_email", context, raw_keys=raw_keys)
    purpose = "forget password"

    return await _queue_email(to_email, subject, html_content, purpose)


async def send_profile_updated_email(to_email: str, first_name: str | None = None) -> bool:
    """Queue Profile Updated email for cron delivery."""
    greeting = _email_greeting(first_name)
    context = {
        "first_name": first_name or "User",
        "greeting": greeting,
    }
    subject, html_content = await render_email_by_name(None, "profile_updated_email", context)
    purpose = "Profile Updated"
    return await _queue_email(to_email, subject, html_content, purpose=purpose)


async def _render_graduation_completion_email(
    session,
    *,
    first_name: str | None,
    university_name: str | None,
) -> tuple[str, str]:
    context = {
        "first_name": first_name or "User",
        "university_name": _graduation_university_name(university_name),
    }
    return await render_email_by_name(session, "graduation_email", context)


async def send_graduation_completion_email(
    to_email: str,
    first_name: str | None = None,
    university_name: str | None = None,
) -> bool:
    """Queue graduation completion email for cron delivery."""
    subject, html_content = await _render_graduation_completion_email(
        None,
        first_name=first_name,
        university_name=university_name,
    )
    purpose = GRADUATION_COMPLETION_PURPOSE
    return await _queue_email(to_email, subject, html_content, purpose=purpose)
