from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.notifications.db_models import (
    NotificationCampaign,
    NotificationCampaignAudience,
)
from common.enums import NotificationCampaignStatus, NotificationCampaignType
from common.time import utc_now


def _apply_campaign_filters(
    stmt,
    *,
    search: str | None = None,
    campaign_type: NotificationCampaignType | None = None,
    status: NotificationCampaignStatus | None = None,
    active_only: bool = True,
):
    if active_only:
        stmt = stmt.where(NotificationCampaign.is_active.is_(True))
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                NotificationCampaign.title.ilike(term),
                NotificationCampaign.message.ilike(term),
            )
        )
    if campaign_type is not None:
        stmt = stmt.where(NotificationCampaign.campaign_type == campaign_type)
    if status is not None:
        stmt = stmt.where(NotificationCampaign.status == status)
    return stmt


async def get_campaign_by_id(
    db: AsyncSession,
    campaign_id: UUID,
    *,
    active_only: bool = False,
) -> NotificationCampaign | None:
    stmt = select(NotificationCampaign).where(NotificationCampaign.id == campaign_id)
    if active_only:
        stmt = stmt.where(NotificationCampaign.is_active.is_(True))
    return (await db.execute(stmt)).scalar_one_or_none()


async def update_campaign(
    db: AsyncSession,
    campaign: NotificationCampaign,
    *,
    notification_type_id: UUID,
    campaign_type: NotificationCampaignType,
    title: str,
    message: str,
    deep_link_payload: dict[str, Any] | None = None,
) -> NotificationCampaign:
    campaign.notification_type_id = notification_type_id
    campaign.campaign_type = campaign_type
    campaign.title = title
    campaign.message = message
    campaign.deep_link_payload = deep_link_payload
    campaign.updated_at = utc_now()
    db.add(campaign)
    await db.flush()
    await db.refresh(campaign)
    return campaign


async def deactivate_campaign(
    db: AsyncSession,
    campaign: NotificationCampaign,
) -> NotificationCampaign:
    campaign.is_active = False
    campaign.updated_at = utc_now()
    db.add(campaign)
    await db.flush()
    await db.refresh(campaign)
    return campaign


async def create_campaign(
    db: AsyncSession,
    *,
    notification_type_id: UUID,
    campaign_type: NotificationCampaignType,
    title: str,
    message: str,
    created_by_admin_id: UUID,
    deep_link_payload: dict[str, Any] | None = None,
    scheduled_at: datetime | None = None,
    status: NotificationCampaignStatus = NotificationCampaignStatus.draft,
    sent_at: datetime | None = None,
) -> NotificationCampaign:
    campaign = NotificationCampaign(
        notification_type_id=notification_type_id,
        campaign_type=campaign_type,
        title=title,
        message=message,
        deep_link_payload=deep_link_payload,
        created_by_admin_id=created_by_admin_id,
        scheduled_at=scheduled_at,
        sent_at=sent_at,
        status=status,
        is_active=True,
    )
    db.add(campaign)
    await db.flush()
    await db.refresh(campaign)
    return campaign


async def update_campaign_status(
    db: AsyncSession,
    campaign: NotificationCampaign,
    *,
    status: NotificationCampaignStatus,
    sent_at: datetime | None = None,
) -> NotificationCampaign:
    campaign.status = status
    campaign.updated_at = utc_now()
    if sent_at is not None:
        campaign.sent_at = sent_at
    db.add(campaign)
    await db.flush()
    await db.refresh(campaign)
    return campaign


async def count_campaigns(
    db: AsyncSession,
    *,
    search: str | None = None,
    campaign_type: NotificationCampaignType | None = None,
    status: NotificationCampaignStatus | None = None,
) -> int:
    stmt = select(func.count()).select_from(NotificationCampaign)
    stmt = _apply_campaign_filters(
        stmt,
        search=search,
        campaign_type=campaign_type,
        status=status,
    )
    return int((await db.execute(stmt)).scalar_one())


async def get_campaigns(
    db: AsyncSession,
    *,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
    campaign_type: NotificationCampaignType | None = None,
    status: NotificationCampaignStatus | None = None,
) -> list[tuple[NotificationCampaign, int]]:
    """Return campaigns with recipient counts, newest first.

    When page or page_size is provided, applies offset/limit.
    When both are None, returns all matching rows.
    """
    recipient_count = func.count(NotificationCampaignAudience.id).label("recipient_count")
    stmt = (
        select(NotificationCampaign, recipient_count)
        .outerjoin(
            NotificationCampaignAudience,
            NotificationCampaignAudience.campaign_id == NotificationCampaign.id,
        )
        .group_by(NotificationCampaign.id)
        .order_by(NotificationCampaign.created_at.desc())
    )
    stmt = _apply_campaign_filters(
        stmt,
        search=search,
        campaign_type=campaign_type,
        status=status,
    )

    if page is not None or page_size is not None:
        resolved_page = page if page is not None else 1
        resolved_page_size = page_size if page_size is not None else 20
        stmt = stmt.offset((resolved_page - 1) * resolved_page_size).limit(resolved_page_size)

    rows = (await db.execute(stmt)).all()
    return [(row[0], int(row[1] or 0)) for row in rows]


async def count_campaign_recipients(
    db: AsyncSession,
    campaign_id: UUID,
) -> int:
    stmt = (
        select(func.count())
        .select_from(NotificationCampaignAudience)
        .where(NotificationCampaignAudience.campaign_id == campaign_id)
    )
    return int((await db.execute(stmt)).scalar_one())
