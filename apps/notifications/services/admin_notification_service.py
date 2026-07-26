from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.notifications.db_models import NotificationCampaign
from apps.notifications.repositories.admin_campaign_repository import (
    count_campaigns,
    create_campaign as persist_campaign,
    get_campaign_by_id,
    get_campaigns,
    update_campaign_status,
)
from apps.notifications.repositories.campaign_audience_repository import (
    create_campaign_audience,
    get_active_fcm_tokens_for_users,
    resolve_announcement_recipients,
    resolve_topic_recipients,
)
from apps.notifications.repositories.notification_repository import (
    bulk_create_notifications,
    get_notification_type_by_name,
)
from apps.notifications.schemas import (
    AdminCampaignListItem,
    AdminCampaignListResponse,
    CreateCampaignData,
    CreateCampaignRequest,
    CreateCampaignResponse,
    CreateCampaignTarget,
)
from common.enums import (
    NotificationCampaignStatus,
    NotificationCampaignType,
    NotificationTargetType,
)
from common.pagination import build_paginated_response
from common.responses import error_response, success_response
from common.time import utc_now
from core.auth.services import send_push_notifications

logger = logging.getLogger(__name__)

CAMPAIGN_CREATED_MESSAGE = "Notification campaign created successfully."
CAMPAIGNS_FETCHED_MESSAGE = "Notification campaigns fetched successfully."
NOTIFICATION_TYPE_MISSING_MESSAGE = "Notification type is not configured."
CAMPAIGN_NOT_FOUND_MESSAGE = "Notification campaign not found."

# Stored on campaign.deep_link_payload so dispatch_campaign(campaign_id) can run
# later from a worker without a dedicated targets column (no schema change).
_TARGETS_STORAGE_KEY = "targets"


def _build_targets_storage(
    targets: list[CreateCampaignTarget],
) -> dict[str, Any] | None:
    if not targets:
        return None
    return {
        _TARGETS_STORAGE_KEY: [
            {"type": target.type.value, "values": list(target.values)}
            for target in targets
        ]
    }


def _parse_stored_targets(
    payload: dict[str, Any] | None,
) -> list[tuple[NotificationTargetType, list[str]]]:
    if not payload:
        return []
    raw_targets = payload.get(_TARGETS_STORAGE_KEY) or []
    parsed: list[tuple[NotificationTargetType, list[str]]] = []
    for item in raw_targets:
        if not isinstance(item, dict):
            continue
        raw_type = item.get("type")
        raw_values = item.get("values") or []
        if not raw_type or not isinstance(raw_values, list):
            continue
        try:
            target_type = NotificationTargetType(raw_type)
        except ValueError:
            logger.warning("Skipping unknown campaign target type: %s", raw_type)
            continue
        values = [str(value).strip() for value in raw_values if value and str(value).strip()]
        if values:
            parsed.append((target_type, values))
    return parsed


async def create_campaign(
    db: AsyncSession,
    *,
    admin_user_id: UUID,
    payload: CreateCampaignRequest,
) -> CreateCampaignResponse:
    """Validate and persist a DRAFT campaign only. Caller should invoke dispatch_campaign."""
    notification_type = await get_notification_type_by_name(
        db,
        payload.campaign_type.value,
    )
    if notification_type is None:
        return error_response(
            NOTIFICATION_TYPE_MISSING_MESSAGE,
            response_cls=CreateCampaignResponse,
        )

    targets_storage = (
        _build_targets_storage(payload.targets)
        if payload.campaign_type == NotificationCampaignType.topic
        else None
    )

    try:
        campaign = await persist_campaign(
            db,
            notification_type_id=notification_type.id,
            campaign_type=payload.campaign_type,
            title=payload.title.strip(),
            message=payload.message.strip(),
            created_by_admin_id=admin_user_id,
            deep_link_payload=targets_storage,
            scheduled_at=None,
            status=NotificationCampaignStatus.draft,
            sent_at=None,
        )
        await db.commit()
        await db.refresh(campaign)
    except IntegrityError:
        await db.rollback()
        return error_response(
            "Failed to create notification campaign.",
            response_cls=CreateCampaignResponse,
        )

    return success_response(
        CAMPAIGN_CREATED_MESSAGE,
        CreateCampaignData(id=campaign.id),
        response_cls=CreateCampaignResponse,
    )


async def dispatch_campaign(
    db: AsyncSession,
    campaign_id: UUID,
) -> None:
    """
    Single entry point for campaign processing.

    Safe to call synchronously today or from a future background worker:
    ``queue.enqueue(dispatch_campaign, campaign_id)``.
    """
    logger.info("Campaign started campaign_id=%s", campaign_id)

    campaign = await _get_campaign(db, campaign_id)
    if campaign is None:
        logger.error("Campaign failed campaign_id=%s reason=not_found", campaign_id)
        raise ValueError(CAMPAIGN_NOT_FOUND_MESSAGE)

    try:
        recipient_user_ids = await _resolve_recipients(db, campaign)
        logger.info(
            "Recipients resolved campaign_id=%s recipient_count=%s",
            campaign_id,
            len(recipient_user_ids),
        )

        await _create_campaign_audience(db, campaign.id, recipient_user_ids)
        notification_count = await _create_notifications(
            db,
            campaign,
            recipient_user_ids,
        )
        logger.info(
            "Notifications created campaign_id=%s notification_count=%s",
            campaign_id,
            notification_count,
        )

        fcm_tokens = await _get_active_installations(db, recipient_user_ids)
        push_result = await _send_push_notifications(campaign, fcm_tokens)
        logger.info(
            "Push notifications sent campaign_id=%s successful_count=%s failed_count=%s",
            campaign_id,
            push_result.get("successful_count", 0),
            push_result.get("failed_count", 0),
        )

        await _update_campaign_status(
            db,
            campaign,
            status=NotificationCampaignStatus.sent,
            sent_at=utc_now(),
        )
        await db.commit()
        logger.info("Campaign completed campaign_id=%s status=SENT", campaign_id)
    except Exception:
        logger.exception("Campaign failed campaign_id=%s", campaign_id)
        try:
            await db.rollback()
            campaign = await _get_campaign(db, campaign_id)
            if campaign is not None:
                await _update_campaign_status(
                    db,
                    campaign,
                    status=NotificationCampaignStatus.failed,
                )
                await db.commit()
        except Exception:
            logger.exception(
                "Failed to mark campaign as FAILED campaign_id=%s",
                campaign_id,
            )
            await db.rollback()
        raise


async def _get_campaign(
    db: AsyncSession,
    campaign_id: UUID,
) -> NotificationCampaign | None:
    return await get_campaign_by_id(db, campaign_id)


async def _resolve_recipients(
    db: AsyncSession,
    campaign: NotificationCampaign,
) -> list[UUID]:
    if campaign.campaign_type == NotificationCampaignType.announcement:
        recipients = await resolve_announcement_recipients(db)
    else:
        targets = _parse_stored_targets(campaign.deep_link_payload)
        recipients = await resolve_topic_recipients(db, targets=targets)
    return list(dict.fromkeys(recipients))


async def _create_campaign_audience(
    db: AsyncSession,
    campaign_id: UUID,
    user_ids: list[UUID],
) -> int:
    return await create_campaign_audience(
        db,
        campaign_id=campaign_id,
        user_ids=user_ids,
    )


async def _create_notifications(
    db: AsyncSession,
    campaign: NotificationCampaign,
    recipient_user_ids: list[UUID],
) -> int:
    return await bulk_create_notifications(
        db,
        recipient_user_ids=recipient_user_ids,
        notification_type_id=campaign.notification_type_id,
        campaign_id=campaign.id,
        title=campaign.title,
        body=campaign.message,
        deep_link_payload=None,
    )


async def _get_active_installations(
    db: AsyncSession,
    user_ids: list[UUID],
) -> list[str]:
    return await get_active_fcm_tokens_for_users(db, user_ids)


async def _send_push_notifications(
    campaign: NotificationCampaign,
    fcm_tokens: list[str],
) -> dict[str, Any]:
    if not fcm_tokens:
        return {"successful_count": 0, "failed_count": 0, "failed_tokens": []}

    data = {
        "notification_type": (
            campaign.campaign_type.value
            if hasattr(campaign.campaign_type, "value")
            else str(campaign.campaign_type)
        ),
        "campaign_id": str(campaign.id),
    }
    return send_push_notifications(
        fcm_tokens,
        campaign.title,
        campaign.message,
        data,
    )


async def _update_campaign_status(
    db: AsyncSession,
    campaign: NotificationCampaign,
    *,
    status: NotificationCampaignStatus,
    sent_at=None,
) -> NotificationCampaign:
    return await update_campaign_status(
        db,
        campaign,
        status=status,
        sent_at=sent_at,
    )


async def list_campaigns(
    db: AsyncSession,
    *,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
    campaign_type: NotificationCampaignType | None = None,
    status: NotificationCampaignStatus | None = None,
) -> AdminCampaignListResponse:
    total_items = await count_campaigns(
        db,
        search=search,
        campaign_type=campaign_type,
        status=status,
    )
    rows = await get_campaigns(
        db,
        page=page,
        page_size=page_size,
        search=search,
        campaign_type=campaign_type,
        status=status,
    )
    items = [
        AdminCampaignListItem(
            id=campaign.id,
            title=campaign.title,
            message=campaign.message,
            campaign_type=campaign.campaign_type,
            status=campaign.status,
            recipient_count=recipient_count,
            scheduled_at=campaign.scheduled_at,
            sent_at=campaign.sent_at,
            created_at=campaign.created_at,
        )
        for campaign, recipient_count in rows
    ]

    paginate = page is not None or page_size is not None
    if paginate:
        resolved_page = page if page is not None else 1
        resolved_page_size = page_size if page_size is not None else 20
        data = build_paginated_response(
            items,
            resolved_page,
            resolved_page_size,
            total_items,
        ).model_dump()
    else:
        data = {"items": [item.model_dump(mode="json") for item in items]}

    return success_response(
        CAMPAIGNS_FETCHED_MESSAGE,
        data,
        response_cls=AdminCampaignListResponse,
    )
