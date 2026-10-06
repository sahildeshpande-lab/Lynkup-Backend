from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from apps.bulk_send.enums import SENDGRID_MAX_PERSONALIZATIONS, EmailCampaignStatus, EmailDeliveryStatus
from apps.bulk_send.models import EmailCampaign, EmailDelivery
from apps.bulk_send.repository import (
    CampaignTerminalResult,
    claim_pending_deliveries,
    get_campaign,
    mark_campaign_processing,
    maybe_complete_campaign,
)
from apps.bulk_send.schemas import BulkBatchSendResult, DeliveryResult, DeliveryStats
from apps.bulk_send.storage import BulkSendStorage, get_bulk_send_storage
from core.database.session import async_session_factory
from core.email_service import (
    build_bulk_email_plain_text,
    send_bulk_campaign_batch,
)

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 3


def _bulk_inbox_subject(name: str | None, subject: str | None) -> str:
    """Inbox subject line: ``Name | Subject`` when both differ."""
    campaign_name = (name or "").strip()
    campaign_subject = (subject or "").strip()
    if campaign_name and campaign_subject and campaign_name != campaign_subject:
        return f"{campaign_name} | {campaign_subject}"
    return campaign_name or campaign_subject or "KampuLynk"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _activity_status_for_campaign(campaign: EmailCampaign) -> str:
    status = campaign.status
    value = status.value if hasattr(status, "value") else str(status)
    if value == EmailCampaignStatus.failed.value:
        return "failed"
    return "delivered"


async def _actor_role_for_user(session, user_id: UUID | None) -> str:
    if user_id is None:
        return "superadmin"
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from apps.accounts.db_models import User

    user = (
        await session.execute(
            select(User).options(selectinload(User.roles)).where(User.id == user_id)
        )
    ).scalars().first()
    role = getattr(user, "role", None) if user is not None else None
    return str(role).strip() if role else "superadmin"


async def log_bulk_email_dispatch_activity(
    session,
    campaign: EmailCampaign,
    stats: DeliveryStats,
) -> None:
    """Record activity-log / admin-notification after send succeeds or fails."""
    from apps.administration.services.admin_activity_log_service import create_admin_activity_log

    activity_status = _activity_status_for_campaign(campaign)
    failed = activity_status == "failed"
    await create_admin_activity_log(
        session,
        user_id=campaign.created_by,
        role=await _actor_role_for_user(session, campaign.created_by),
        action="fail" if failed else "create",
        module="bulk_email",
        record_id=campaign.id,
        description="failed to send a bulk email" if failed else "sent a bulk email",
        metadata={
            "old": None,
            "new": {
                "campaign_id": str(campaign.id),
                "name": campaign.name,
                "subject": campaign.subject,
                "status": activity_status,
                "total_recipients": stats.total,
                "sent": stats.sent,
                "failed": stats.failed,
            },
        },
        commit=True,
    )


async def _log_terminal_campaign_activity(
    session,
    completion: CampaignTerminalResult | None,
) -> None:
    if completion is None:
        return
    try:
        await log_bulk_email_dispatch_activity(session, completion.campaign, completion.stats)
    except Exception:
        logger.exception(
            "Failed to write bulk email activity log campaign_id=%s",
            completion.campaign.id,
        )


def _chunked(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        return [items] if items else []
    return [items[i : i + size] for i in range(0, len(items), size)]


def _load_attachment_payloads(
    campaign: EmailCampaign,
    storage: BulkSendStorage,
    cache: dict[str, tuple[bytes, str, str]],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for item in campaign.attachments or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("storage_key") or "").strip()
        if not key:
            continue
        if key not in cache:
            content = storage.download(key)
            cache[key] = (
                content,
                str(item.get("file_name") or key.rsplit("/", 1)[-1]),
                str(item.get("content_type") or "application/octet-stream"),
            )
        content, file_name, content_type = cache[key]
        payloads.append(
            {
                "content": content,
                "file_name": file_name,
                "content_type": content_type,
            }
        )
    return payloads


def _recipients_payload(campaign: EmailCampaign, deliveries: list[EmailDelivery]) -> list[dict]:
    return [
        {
            "email": delivery.email,
            "campaign_id": campaign.id,
            "delivery_id": delivery.id,
        }
        for delivery in deliveries
    ]


async def _finalize_delivery(
    delivery_id: UUID,
    campaign_id: UUID,
    result: DeliveryResult,
    attempt_count: int,
    session_factory=None,
) -> None:
    from sqlmodel import select

    factory = session_factory if session_factory is not None else async_session_factory
    async with factory() as session:
        delivery = (
            await session.execute(select(EmailDelivery).where(EmailDelivery.id == delivery_id))
        ).scalars().first()
        if delivery is None:
            return

        now = utc_now()
        delivery.updated_at = now
        if result.success:
            delivery.status = EmailDeliveryStatus.sent
            delivery.sendgrid_message_id = result.sendgrid_message_id
            delivery.delivered_at = now
            delivery.failure_reason = None
        else:
            delivery.failure_reason = result.failure_reason or "Failed to send email"
            if result.retryable and attempt_count < MAX_DELIVERY_ATTEMPTS:
                delivery.status = EmailDeliveryStatus.pending
            else:
                delivery.status = EmailDeliveryStatus.failed

        session.add(delivery)
        await session.commit()
        completion = await maybe_complete_campaign(session, campaign_id)
        await _log_terminal_campaign_activity(session, completion)


async def _finalize_batch(
    deliveries: list[EmailDelivery],
    campaign_id: UUID,
    result: BulkBatchSendResult,
    session_factory=None,
) -> None:
    """Apply one SendGrid batch outcome to every delivery in the batch.

    SendGrid Mail Send returns a single request-level ``X-Message-Id`` for the
    whole personalization batch, not one ID per recipient. We store that same
    value on every delivery when present. Per-delivery correlation for future
    Event Webhooks uses ``custom_args.campaign_id`` / ``custom_args.delivery_id``.
    """
    from sqlmodel import select

    delivery_ids = [d.id for d in deliveries]
    attempt_by_id = {d.id: int(d.attempt_count or 0) for d in deliveries}

    factory = session_factory if session_factory is not None else async_session_factory
    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    select(EmailDelivery).where(EmailDelivery.id.in_(delivery_ids))
                )
            ).scalars().all()
        )
        now = utc_now()
        for delivery in rows:
            delivery.updated_at = now
            attempt_count = attempt_by_id.get(delivery.id, int(delivery.attempt_count or 0))
            if result.success:
                delivery.status = EmailDeliveryStatus.sent
                # Batch-level X-Message-Id (not unique per personalization).
                delivery.sendgrid_message_id = result.sendgrid_message_id
                delivery.delivered_at = now
                delivery.failure_reason = None
            else:
                delivery.failure_reason = result.failure_reason or "Failed to send email"
                if result.retryable and attempt_count < MAX_DELIVERY_ATTEMPTS:
                    delivery.status = EmailDeliveryStatus.pending
                else:
                    delivery.status = EmailDeliveryStatus.failed
            session.add(delivery)
        await session.commit()
        completion = await maybe_complete_campaign(session, campaign_id)
        await _log_terminal_campaign_activity(session, completion)


async def _eligible_bulk_email_user_ids(user_ids: list[UUID]) -> list[UUID]:
    """Return user IDs allowed to receive bulk email (respects email_preferences)."""
    from apps.notifications.email_preferences import EMAIL_PREF_BULK_EMAIL
    from apps.notifications.repositories.notification_repository import (
        filter_users_eligible_for_email_preference,
    )

    async with async_session_factory() as session:
        return await filter_users_eligible_for_email_preference(
            session,
            user_ids,
            preference=EMAIL_PREF_BULK_EMAIL,
        )


async def _send_campaign_batches(
    campaign: EmailCampaign,
    deliveries: list[EmailDelivery],
    *,
    attachments: list[dict[str, Any]],
    session_factory=None,
) -> int:
    """Send claimed deliveries for one campaign in ≤1000 personalization batches."""
    eligible_ids = set(
        await _eligible_bulk_email_user_ids([d.user_id for d in deliveries])
    )

    opted_out = [d for d in deliveries if d.user_id not in eligible_ids]
    eligible = [d for d in deliveries if d.user_id in eligible_ids]
    if opted_out:
        await _finalize_batch(
            opted_out,
            campaign.id,
            BulkBatchSendResult(
                success=False,
                failure_reason="bulk_email preference disabled",
                retryable=False,
                recipient_count=len(opted_out),
            ),
        )

    processed = len(opted_out)
    from apps.bulk_send.email_template import render_bulk_campaign_html

    # Branded shell from DB template ``bulk_campaign_email``.
    rendered_campaign_html = await render_bulk_campaign_html(
        name=campaign.name or "",
        subject=campaign.subject or "",
        body_html=campaign.body_html or "",
    )
    inbox_subject = _bulk_inbox_subject(campaign.name, campaign.subject)

    for batch in _chunked(eligible, SENDGRID_MAX_PERSONALIZATIONS):
        try:
            result = await send_bulk_campaign_batch(
                subject=inbox_subject,
                html_body=rendered_campaign_html,
                plain_text=build_bulk_email_plain_text(
                    campaign.name,
                    campaign.subject,
                    body_text=campaign.body_text,
                    body_html=campaign.body_html,
                ),
                recipients=_recipients_payload(campaign, batch),
                attachments=attachments,
            )

        except Exception as exc:
            logger.exception(
                "Unexpected bulk batch failure campaign_id=%s recipients=%d",
                campaign.id,
                len(batch),
            )
            result = BulkBatchSendResult(
                success=False,
                failure_reason=str(exc),
                retryable=True,
                recipient_count=len(batch),
            )

        await _finalize_batch(
            batch,
            campaign.id,
            result,
            session_factory=session_factory,
        )
        processed += len(batch)
    return processed


async def process_pending_bulk_deliveries(
    limit: int = SENDGRID_MAX_PERSONALIZATIONS,
    *,
    session_factory=None,
) -> int:
    """Claim pending deliveries and send via SendGrid personalization batches."""
    storage = get_bulk_send_storage()
    attachment_cache: dict[str, tuple[bytes, str, str]] = {}
    factory = session_factory if session_factory is not None else async_session_factory

    async with factory() as session:
        deliveries = await claim_pending_deliveries(session, limit=limit)

    if not deliveries:
        return 0

    by_campaign: dict[UUID, list[EmailDelivery]] = defaultdict(list)
    for delivery in deliveries:
        by_campaign[delivery.campaign_id].append(delivery)

    for campaign_id in by_campaign:
        async with factory() as session:
            await mark_campaign_processing(session, campaign_id)

    campaigns: dict[UUID, EmailCampaign] = {}
    async with factory() as session:
        for campaign_id in by_campaign:
            campaign = await get_campaign(session, campaign_id)
            if campaign is not None:
                campaigns[campaign_id] = campaign

    processed = 0
    for campaign_id, campaign_deliveries in by_campaign.items():
        campaign = campaigns.get(campaign_id)
        if campaign is None:
            for delivery in campaign_deliveries:
                await _finalize_delivery(
                    delivery.id,
                    campaign_id,
                    DeliveryResult(
                        success=False,
                        failure_reason="Parent campaign not found",
                        retryable=False,
                    ),
                    delivery.attempt_count,
                    session_factory=factory,
                )
                processed += 1
            continue

        attachments = _load_attachment_payloads(campaign, storage, attachment_cache)
        processed += await _send_campaign_batches(
            campaign,
            campaign_deliveries,
            attachments=attachments,
            session_factory=factory,
        )

    logger.info(
        "Bulk email cron processed %d deliveries across %d campaign(s)",
        processed,
        len(by_campaign),
    )
    return processed
