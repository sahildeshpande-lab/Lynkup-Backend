from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.bulk_send.enums import EmailCampaignStatus, EmailDeliveryStatus
from apps.bulk_send.models import EmailCampaign, EmailDelivery
from apps.bulk_send.schemas import DeliveryStats


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_campaign(session: AsyncSession, campaign_id: UUID) -> EmailCampaign | None:
    stmt = select(EmailCampaign).where(EmailCampaign.id == campaign_id)
    return (await session.execute(stmt)).scalars().first()


async def list_campaigns(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
) -> tuple[list[EmailCampaign], int]:
    count_stmt = select(func.count()).select_from(EmailCampaign)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)

    stmt = (
        select(EmailCampaign)
        .order_by(EmailCampaign.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list((await session.execute(stmt)).scalars().all())
    return items, total


async def delivery_stats_for_campaign(
    session: AsyncSession,
    campaign_id: UUID,
) -> DeliveryStats:
    stmt = (
        select(
            func.count().label("total"),
            func.coalesce(
                func.sum(case((EmailDelivery.status == EmailDeliveryStatus.pending, 1), else_=0)),
                0,
            ).label("pending"),
            func.coalesce(
                func.sum(case((EmailDelivery.status == EmailDeliveryStatus.processing, 1), else_=0)),
                0,
            ).label("processing"),
            func.coalesce(
                func.sum(case((EmailDelivery.status == EmailDeliveryStatus.sent, 1), else_=0)),
                0,
            ).label("sent"),
            func.coalesce(
                func.sum(case((EmailDelivery.status == EmailDeliveryStatus.failed, 1), else_=0)),
                0,
            ).label("failed"),
        )
        .where(EmailDelivery.campaign_id == campaign_id)
    )
    row = (await session.execute(stmt)).one()
    return DeliveryStats(
        total=int(row.total or 0),
        pending=int(row.pending or 0),
        processing=int(row.processing or 0),
        sent=int(row.sent or 0),
        failed=int(row.failed or 0),
    )


async def claim_pending_deliveries(
    session: AsyncSession,
    *,
    limit: int = 10,
) -> list[EmailDelivery]:
    """Claim pending deliveries with row locks so concurrent cron workers do not double-send."""
    active_statuses = (EmailCampaignStatus.queued, EmailCampaignStatus.processing)
    base = (
        select(EmailDelivery)
        .join(EmailCampaign, EmailCampaign.id == EmailDelivery.campaign_id)
        .where(EmailDelivery.status == EmailDeliveryStatus.pending)
        .where(EmailCampaign.status.in_(active_statuses))
        .order_by(EmailDelivery.created_at.asc())
        .limit(limit)
    )
    # Prefer SKIP LOCKED when the dialect supports it (PostgreSQL).
    try:
        stmt = base.with_for_update(skip_locked=True, of=EmailDelivery)
        deliveries = list((await session.execute(stmt)).scalars().all())
    except Exception:
        await session.rollback()
        stmt = base.with_for_update(skip_locked=True)
        try:
            deliveries = list((await session.execute(stmt)).scalars().all())
        except Exception:
            await session.rollback()
            deliveries = list((await session.execute(base)).scalars().all())
    now = utc_now()
    for delivery in deliveries:
        delivery.status = EmailDeliveryStatus.processing
        delivery.attempt_count = int(delivery.attempt_count or 0) + 1
        delivery.last_attempt_at = now
        delivery.updated_at = now
        session.add(delivery)
    if deliveries:
        await session.commit()
        for delivery in deliveries:
            await session.refresh(delivery)
    return deliveries


async def mark_campaign_processing(session: AsyncSession, campaign_id: UUID) -> None:
    campaign = await get_campaign(session, campaign_id)
    if campaign is None:
        return
    if campaign.status == EmailCampaignStatus.queued:
        campaign.status = EmailCampaignStatus.processing
        campaign.started_at = campaign.started_at or utc_now()
        campaign.updated_at = utc_now()
        session.add(campaign)
        await session.commit()


async def maybe_complete_campaign(session: AsyncSession, campaign_id: UUID) -> None:
    stats = await delivery_stats_for_campaign(session, campaign_id)
    if stats.pending > 0 or stats.processing > 0:
        return
    campaign = await get_campaign(session, campaign_id)
    if campaign is None:
        return
    if campaign.status == EmailCampaignStatus.completed:
        return
    campaign.status = EmailCampaignStatus.completed
    campaign.completed_at = utc_now()
    campaign.updated_at = utc_now()
    session.add(campaign)
    await session.commit()


async def bulk_delivery_stats_by_campaign(
    session: AsyncSession,
    campaign_ids: list[UUID],
) -> dict[UUID, DeliveryStats]:
    if not campaign_ids:
        return {}
    stmt = (
        select(
            EmailDelivery.campaign_id,
            func.count().label("total"),
            func.coalesce(
                func.sum(case((EmailDelivery.status == EmailDeliveryStatus.pending, 1), else_=0)),
                0,
            ).label("pending"),
            func.coalesce(
                func.sum(case((EmailDelivery.status == EmailDeliveryStatus.processing, 1), else_=0)),
                0,
            ).label("processing"),
            func.coalesce(
                func.sum(case((EmailDelivery.status == EmailDeliveryStatus.sent, 1), else_=0)),
                0,
            ).label("sent"),
            func.coalesce(
                func.sum(case((EmailDelivery.status == EmailDeliveryStatus.failed, 1), else_=0)),
                0,
            ).label("failed"),
        )
        .where(EmailDelivery.campaign_id.in_(campaign_ids))
        .group_by(EmailDelivery.campaign_id)
    )
    rows = (await session.execute(stmt)).all()
    result: dict[UUID, DeliveryStats] = defaultdict(
        lambda: DeliveryStats(total=0, pending=0, processing=0, sent=0, failed=0)
    )
    for row in rows:
        result[row.campaign_id] = DeliveryStats(
            total=int(row.total or 0),
            pending=int(row.pending or 0),
            processing=int(row.processing or 0),
            sent=int(row.sent or 0),
            failed=int(row.failed or 0),
        )
    return dict(result)
