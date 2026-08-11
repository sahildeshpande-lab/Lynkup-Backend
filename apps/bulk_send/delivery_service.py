from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from apps.bulk_send.enums import EmailDeliveryStatus
from apps.bulk_send.models import EmailCampaign, EmailDelivery
from apps.bulk_send.repository import (
    claim_pending_deliveries,
    get_campaign,
    mark_campaign_processing,
    maybe_complete_campaign,
)
from apps.bulk_send.schemas import DeliveryResult
from apps.bulk_send.storage import BulkSendStorage, get_bulk_send_storage
from core.database.session import async_session_factory
from core.email_service import send_bulk_campaign_email

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 3


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


async def send_bulk_campaign_delivery(
    campaign: EmailCampaign,
    delivery: EmailDelivery,
    *,
    attachments: list[dict[str, Any]] | None = None,
) -> DeliveryResult:
    """Send one campaign delivery through the shared SendGrid path."""
    return await send_bulk_campaign_email(
        to_email=delivery.email,
        subject=campaign.subject,
        html_body=campaign.body_html,
        plain_text=campaign.body_text,
        attachments=attachments or [],
    )


async def _finalize_delivery(
    delivery_id: UUID,
    campaign_id: UUID,
    result: DeliveryResult,
    attempt_count: int,
) -> None:
    from sqlmodel import select

    async with async_session_factory() as session:
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
        await maybe_complete_campaign(session, campaign_id)


async def process_pending_bulk_deliveries(limit: int = 10) -> int:
    """Claim and send a bounded batch of pending bulk email deliveries."""
    storage = get_bulk_send_storage()
    attachment_cache: dict[str, tuple[bytes, str, str]] = {}

    async with async_session_factory() as session:
        deliveries = await claim_pending_deliveries(session, limit=limit)

    if not deliveries:
        return 0

    campaign_ids = {delivery.campaign_id for delivery in deliveries}
    for campaign_id in campaign_ids:
        async with async_session_factory() as session:
            await mark_campaign_processing(session, campaign_id)

    campaigns: dict[UUID, EmailCampaign] = {}
    async with async_session_factory() as session:
        for campaign_id in campaign_ids:
            campaign = await get_campaign(session, campaign_id)
            if campaign is not None:
                campaigns[campaign_id] = campaign

    processed = 0
    for delivery in deliveries:
        campaign = campaigns.get(delivery.campaign_id)
        if campaign is None:
            await _finalize_delivery(
                delivery.id,
                delivery.campaign_id,
                DeliveryResult(
                    success=False,
                    failure_reason="Parent campaign not found",
                    retryable=False,
                ),
                delivery.attempt_count,
            )
            processed += 1
            continue

        try:
            attachments = _load_attachment_payloads(campaign, storage, attachment_cache)
            result = await send_bulk_campaign_delivery(
                campaign,
                delivery,
                attachments=attachments,
            )
        except Exception as exc:
            logger.exception(
                "Unexpected bulk delivery failure delivery_id=%s campaign_id=%s",
                delivery.id,
                delivery.campaign_id,
            )
            result = DeliveryResult(
                success=False,
                failure_reason=str(exc),
                retryable=True,
            )

        await _finalize_delivery(
            delivery.id,
            delivery.campaign_id,
            result,
            delivery.attempt_count,
        )
        processed += 1

    logger.info("Bulk email cron processed %d deliveries", processed)
    return processed
