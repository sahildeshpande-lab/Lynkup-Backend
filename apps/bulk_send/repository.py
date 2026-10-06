from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.sql.expression import literal_column
from sqlalchemy.ext.asyncio import AsyncSession

from apps.bulk_send.enums import (
    SENDGRID_MAX_PERSONALIZATIONS,
    EmailCampaignStatus,
    EmailDeliveryStatus,
)
from apps.bulk_send.models import EmailCampaign, EmailDelivery
from apps.bulk_send.schemas import DeliveryStats
from apps.profiles.db_models import Profile


_TERMINAL_CAMPAIGN_STATUSES = (
    EmailCampaignStatus.completed,
    EmailCampaignStatus.failed,
)


class CampaignTerminalResult(NamedTuple):
    campaign: EmailCampaign
    stats: DeliveryStats


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_campaign(session: AsyncSession, campaign_id: UUID) -> EmailCampaign | None:
    stmt = select(EmailCampaign).where(EmailCampaign.id == campaign_id)
    return (await session.execute(stmt)).scalars().first()


def _campaign_search_clause(search: str | None):
    """Match campaign name, email title (subject), or body text.
    
    1. Converts user search query to lowercase (e.g. 'Bulk Email' -> 'bulk email').
    2. Converts DB column values to lowercase using func.lower() (e.g. 'BULK EMAIL' -> 'bulk email').
    3. Matches the converted lowercase values on both sides across name, subject, and body_text.
    """
    term = (search or "").strip().lower()
    if not term:
        return None
    pattern = f"%{term}%"
    return or_(
        func.lower(EmailCampaign.name).like(pattern),
        func.lower(EmailCampaign.subject).like(pattern),
        func.lower(func.coalesce(EmailCampaign.body_text, "")).like(pattern),
    )


async def list_campaigns(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    search: str | None = None,
) -> tuple[list[EmailCampaign], int]:
    search_clause = _campaign_search_clause(search)
    count_stmt = select(func.count()).select_from(EmailCampaign)
    if search_clause is not None:
        count_stmt = count_stmt.where(search_clause)
    total = int((await session.execute(count_stmt)).scalar_one() or 0)

    stmt = select(EmailCampaign)
    if search_clause is not None:
        stmt = stmt.where(search_clause)
    stmt = (
        stmt.order_by(EmailCampaign.created_at.desc())
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


def _recipient_name_search_clause(search: str | None):
    """Match recipient first_name, last_name, or full name (case-insensitive)."""
    term = (search or "").strip().lower()
    if not term:
        return None
    pattern = f"%{term}%"
    full_name = func.lower(
        func.concat(
            func.coalesce(Profile.first_name, ""),
            " ",
            func.coalesce(Profile.last_name, ""),
        )
    )
    return or_(
        func.lower(func.coalesce(Profile.first_name, "")).like(pattern),
        func.lower(func.coalesce(Profile.last_name, "")).like(pattern),
        full_name.like(pattern),
    )


async def get_campaign_deliveries_with_profiles(
    session: AsyncSession,
    campaign_id: UUID,
    *,
    search: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> tuple[list[tuple[EmailDelivery, Profile | None]], int]:
    search_clause = _recipient_name_search_clause(search)
    base = (
        select(EmailDelivery, Profile)
        .outerjoin(Profile, Profile.user_id == EmailDelivery.user_id)
        .where(EmailDelivery.campaign_id == campaign_id)
    )
    if search_clause is not None:
        base = base.where(search_clause)

    count_stmt = select(func.count()).select_from(base.subquery())
    total = int((await session.execute(count_stmt)).scalar_one() or 0)

    stmt = base.order_by(EmailDelivery.created_at.asc(), EmailDelivery.id.asc())
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    rows = (await session.execute(stmt)).all()
    return [(row.EmailDelivery, row.Profile) for row in rows], total


async def claim_pending_deliveries(
    session: AsyncSession,
    *,
    limit: int = SENDGRID_MAX_PERSONALIZATIONS,
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


async def maybe_complete_campaign(
    session: AsyncSession, campaign_id: UUID
) -> CampaignTerminalResult | None:
    """Mark the campaign delivered or failed when no deliveries remain in flight.

    Returns the campaign and stats only when it newly becomes terminal so
    callers can write an admin activity log, matching push campaigns.
    """
    stats = await delivery_stats_for_campaign(session, campaign_id)
    if stats.pending > 0 or stats.processing > 0:
        return None
    campaign = await get_campaign(session, campaign_id)
    if campaign is None:
        return None
    if campaign.status in _TERMINAL_CAMPAIGN_STATUSES:
        return None
    campaign.status = (
        EmailCampaignStatus.failed
        if stats.sent == 0 and stats.failed > 0
        else EmailCampaignStatus.completed
    )
    campaign.completed_at = utc_now()
    campaign.updated_at = utc_now()
    session.add(campaign)
    await session.commit()
    return CampaignTerminalResult(campaign=campaign, stats=stats)


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
