from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.notifications.db_models import NotificationCampaign
from apps.notifications.repositories.admin_campaign_repository import (
    count_campaigns,
    create_campaign as persist_campaign,
    deactivate_campaign,
    get_campaign_by_id,
    get_campaigns,
    update_campaign as persist_campaign_update,
    update_campaign_status,
)
from apps.notifications.repositories.campaign_audience_repository import (
    create_campaign_audience,
    get_active_push_targets_for_users,
    resolve_announcement_recipients,
    resolve_topic_recipients,
)
from apps.notifications.repositories.notification_repository import (
    create_broadcast_notification,
    filter_users_eligible_for_push,
    get_notification_by_campaign_id,
    get_notification_type_by_name,
    update_notification_content,
)
from apps.notifications.schemas import (
    AdminCampaignListItem,
    AdminCampaignListResponse,
    CampaignTargetResponse,
    CreateCampaignData,
    CreateCampaignRequest,
    CreateCampaignResponse,
    CreateCampaignTarget,
    DeleteCampaignResponse,
    UpdateCampaignRequest,
    UpdateCampaignResponse,
)
from apps.notifications.services.topic_service import resolve_firebase_topics_from_targets
from apps.notifications.services.notification_payload_builder import (
    NotificationPayloadBuilder,
)
from common.enums import (
    NotificationCampaignStatus,
    NotificationCampaignType,
    NotificationTargetType,
)
from common.pagination import build_paginated_response
from common.responses import error_response, success_response
from common.time import utc_now
from core.push import send_push_to_devices

logger = logging.getLogger(__name__)

# Stored on campaign.deep_link_payload so dispatch_campaign(campaign_id) can run
# later from a worker without a dedicated targets column (no schema change).
_TARGETS_STORAGE_KEY = "targets"
_FIREBASE_TOPICS_KEY = "firebase_topics"
_BROADCAST_FLAG_KEY = "broadcast"


def _build_targets_storage(
    targets: list[CreateCampaignTarget],
) -> dict[str, Any]:
    """Persist targets exactly as submitted (POST schema shape)."""
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


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value).strip())
    except (TypeError, ValueError):
        return None


def _map_resolved_values(
    values: list[str],
    *,
    id_to_label: dict[str, str],
) -> list[str]:
    """Replace IDs with labels when present; keep original value otherwise."""
    resolved: list[str] = []
    for value in values:
        key = str(value).strip().lower()
        label = id_to_label.get(key)
        resolved.append(label if label else value)
    return resolved


async def _resolve_target_values(
    db: AsyncSession,
    *,
    target_type: NotificationTargetType,
    values: list[str],
) -> list[str]:
    """
    Resolve stored campaign target IDs into their associated display values.

    Country uses ``countries.name``. Unknown/unresolvable values are left as-is.
    """
    if not values:
        return []

    if target_type == NotificationTargetType.country:
        from apps.profiles.db_models import Country

        country_ids = [uid for uid in (_parse_uuid(v) for v in values) if uid is not None]
        if not country_ids:
            return values

        rows = (
            await db.execute(select(Country.id, Country.name).where(Country.id.in_(country_ids)))
        ).all()
        id_to_label = {
            str(row_id).lower(): (name or "").strip()
            for row_id, name in rows
            if (name or "").strip()
        }
        return _map_resolved_values(values, id_to_label=id_to_label)

    if target_type == NotificationTargetType.university:
        from apps.profiles.db_models import University

        university_ids = [uid for uid in (_parse_uuid(v) for v in values) if uid is not None]
        if not university_ids:
            return values

        rows = (
            await db.execute(
                select(University.id, University.name).where(University.id.in_(university_ids))
            )
        ).all()
        id_to_label = {
            str(row_id).lower(): (name or "").strip()
            for row_id, name in rows
            if (name or "").strip()
        }
        return _map_resolved_values(values, id_to_label=id_to_label)

    if target_type == NotificationTargetType.interests:
        from apps.profiles.db_models import AcademicInterest

        interest_ids: list[int] = []
        for v in values:
            try:
                interest_ids.append(int(str(v).strip()))
            except (TypeError, ValueError):
                continue
        if not interest_ids:
            return values

        rows = (
            await db.execute(
                select(AcademicInterest.id, AcademicInterest.name).where(
                    AcademicInterest.id.in_(interest_ids)
                )
            )
        ).all()
        id_to_label = {
            str(row_id): (name or "").strip()
            for row_id, name in rows
            if (name or "").strip()
        }
        return _map_resolved_values(values, id_to_label=id_to_label)

    if target_type == NotificationTargetType.hashtags:
        from apps.feed.db_models import Hashtag

        hashtag_ids = [uid for uid in (_parse_uuid(v) for v in values) if uid is not None]
        if not hashtag_ids:
            return values

        rows = (
            await db.execute(select(Hashtag.id, Hashtag.tag).where(Hashtag.id.in_(hashtag_ids)))
        ).all()
        id_to_label = {
            str(row_id).lower(): (tag or "").strip()
            for row_id, tag in rows
            if (tag or "").strip()
        }
        return _map_resolved_values(values, id_to_label=id_to_label)

    if target_type == NotificationTargetType.education_level:
        from apps.profiles.db_models import EducationLevel

        level_ids: list[int] = []
        for v in values:
            try:
                level_ids.append(int(str(v).strip()))
            except (TypeError, ValueError):
                continue
        if not level_ids:
            return values

        rows = (
            await db.execute(
                select(EducationLevel.id, EducationLevel.name).where(
                    EducationLevel.id.in_(level_ids)
                )
            )
        ).all()
        id_to_label = {
            str(row_id): (name or "").strip()
            for row_id, name in rows
            if (name or "").strip()
        }
        return _map_resolved_values(values, id_to_label=id_to_label)

    # MAJOR/MINOR are already stored as human-readable strings.
    return values


async def _serialize_stored_targets(
    payload: dict[str, Any] | None,
    db: AsyncSession,
) -> list[CampaignTargetResponse]:
    """
    Return stored campaign targets without recipient resolution or reshaping.

    Always returns a list (empty when none were stored).
    """
    if not payload:
        return []
    raw_targets = payload.get(_TARGETS_STORAGE_KEY)
    if not isinstance(raw_targets, list):
        return []

    serialized: list[CampaignTargetResponse] = []
    for item in raw_targets:
        if not isinstance(item, dict):
            continue
        raw_type = item.get("type")
        raw_values = item.get("values")
        if raw_type is None or not isinstance(raw_values, list):
            continue
        try:
            target_type = NotificationTargetType(raw_type)
        except ValueError:
            logger.warning("Skipping unknown stored campaign target type: %s", raw_type)
            continue

        resolved_values = await _resolve_target_values(
            db,
            target_type=target_type,
            values=[str(value) for value in raw_values],
        )
        serialized.append(
            CampaignTargetResponse(
                type=target_type,
                values=resolved_values,
            )
        )
    return serialized


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
            "Notification type is not configured.",
            response_cls=CreateCampaignResponse,
        )

    targets_storage = _build_targets_storage(payload.targets)

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
        "Notification campaign created successfully.",
        CreateCampaignData(id=campaign.id),
        response_cls=CreateCampaignResponse,
    )


async def update_campaign(
    db: AsyncSession,
    *,
    payload: UpdateCampaignRequest,
) -> UpdateCampaignResponse:
    """Update an active campaign. Does not re-dispatch push notifications."""
    campaign = await get_campaign_by_id(db, payload.id, active_only=True)
    if campaign is None:
        return error_response(
            "Notification campaign not found.",
            response_cls=UpdateCampaignResponse,
        )

    notification_type = await get_notification_type_by_name(
        db,
        payload.campaign_type.value,
    )
    if notification_type is None:
        return error_response(
            "Notification type is not configured.",
            response_cls=UpdateCampaignResponse,
        )

    targets_storage = _build_targets_storage(payload.targets)
    title = payload.title.strip()
    message = payload.message.strip()

    try:
        campaign = await persist_campaign_update(
            db,
            campaign,
            notification_type_id=notification_type.id,
            campaign_type=payload.campaign_type,
            title=title,
            message=message,
            deep_link_payload=targets_storage,
        )
        await _sync_broadcast_notification(
            db,
            campaign,
            title=title,
            message=message,
            targets_storage=targets_storage,
        )
        await db.commit()
        await db.refresh(campaign)
    except IntegrityError:
        await db.rollback()
        return error_response(
            "Failed to update notification campaign.",
            response_cls=UpdateCampaignResponse,
        )

    return success_response(
        "Notification campaign updated successfully.",
        CreateCampaignData(id=campaign.id),
        response_cls=UpdateCampaignResponse,
    )


async def delete_campaign(
    db: AsyncSession,
    *,
    campaign_id: UUID,
) -> DeleteCampaignResponse:
    """Soft-delete a campaign by setting is_active=false."""
    campaign = await get_campaign_by_id(db, campaign_id)
    if campaign is None or not campaign.is_active:
        return error_response(
            "Notification campaign not found.",
            response_cls=DeleteCampaignResponse,
        )

    try:
        await deactivate_campaign(db, campaign)
        await db.commit()
        await db.refresh(campaign)
    except IntegrityError:
        await db.rollback()
        return error_response(
            "Failed to delete notification campaign.",
            response_cls=DeleteCampaignResponse,
        )

    return success_response(
        "Notification campaign deleted successfully.",
        CreateCampaignData(id=campaign.id),
        response_cls=DeleteCampaignResponse,
    )


async def _sync_broadcast_notification(
    db: AsyncSession,
    campaign: NotificationCampaign,
    *,
    title: str,
    message: str,
    targets_storage: dict[str, Any],
) -> None:
    notification = await get_notification_by_campaign_id(db, campaign.id)
    if notification is None:
        return

    deep_link = dict(notification.deep_link_payload or {})
    deep_link[_TARGETS_STORAGE_KEY] = targets_storage.get(_TARGETS_STORAGE_KEY, [])
    await update_notification_content(
        db,
        notification,
        title=title,
        body=message,
        deep_link_payload=deep_link,
    )


async def dispatch_campaign(
    db: AsyncSession,
    campaign_id: UUID,
) -> None:
    """
    Single entry point for campaign processing.

    Creates exactly one broadcast notification row for the campaign, writes
    audience audit logs where applicable, and delivers push via device tokens
    (one send per token). TOPIC campaigns never fan out to multiple Firebase
    topics, so a user subscribed to several matching topics still gets one push.
    Never inserts one notification row per recipient.
    """
    logger.info("Campaign started campaign_id=%s", campaign_id)

    campaign = await _get_campaign(db, campaign_id)
    if campaign is None:
        logger.error("Campaign failed campaign_id=%s reason=not_found", campaign_id)
        raise ValueError("Notification campaign not found.")

    try:
        if campaign.campaign_type == NotificationCampaignType.announcement:
            await _dispatch_announcement(db, campaign)
        else:
            await _dispatch_topic(db, campaign)

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


async def _dispatch_announcement(
    db: AsyncSession,
    campaign: NotificationCampaign,
) -> None:
    recipient_user_ids = list(
        dict.fromkeys(await resolve_announcement_recipients(db))
    )
    logger.info(
        "Announcement recipients resolved campaign_id=%s recipient_count=%s",
        campaign.id,
        len(recipient_user_ids),
    )

    await create_campaign_audience(
        db,
        campaign_id=campaign.id,
        user_ids=recipient_user_ids,
    )
    logger.info(
        "Audience audit written campaign_id=%s audience_count=%s",
        campaign.id,
        len(recipient_user_ids),
    )

    notification = await create_broadcast_notification(
        db,
        owner_user_id=campaign.created_by_admin_id,
        notification_type_id=campaign.notification_type_id,
        campaign_id=campaign.id,
        title=campaign.title,
        body=campaign.message,
        deep_link_payload={
            _BROADCAST_FLAG_KEY: True,
            "campaign_type": NotificationCampaignType.announcement.value,
            "campaign_id": str(campaign.id),
        },
    )
    data_payload = NotificationPayloadBuilder.build(
        notification_type=NotificationCampaignType.announcement.value,
        notification_id=notification.id,
        extra=notification.deep_link_payload,
    )
    notification.deep_link_payload = data_payload
    db.add(notification)
    await db.flush()
    logger.info("Broadcast notification created campaign_id=%s", campaign.id)

    push_eligible_user_ids = await filter_users_eligible_for_push(
        db,
        recipient_user_ids,
        category=NotificationCampaignType.announcement.value,
    )
    logger.info(
        "Announcement push eligibility campaign_id=%s recipient_count=%s push_eligible_count=%s",
        campaign.id,
        len(recipient_user_ids),
        len(push_eligible_user_ids),
    )
    fcm_tokens = await get_active_push_targets_for_users(db, push_eligible_user_ids)
    push_result = await _send_token_push(campaign, fcm_tokens, data=data_payload)
    logger.info(
        "Announcement push sent campaign_id=%s successful_count=%s failed_count=%s",
        campaign.id,
        push_result.get("successful_count", 0),
        push_result.get("failed_count", 0),
    )


async def _dispatch_topic(
    db: AsyncSession,
    campaign: NotificationCampaign,
) -> None:
    targets = _parse_stored_targets(campaign.deep_link_payload)
    firebase_topics = await resolve_firebase_topics_from_targets(db, targets)
    logger.info(
        "Topic campaign firebase topics resolved campaign_id=%s topics=%s",
        campaign.id,
        sorted(firebase_topics),
    )

    recipient_user_ids: list[UUID] = []
    try:
        recipient_user_ids = list(
            dict.fromkeys(await resolve_topic_recipients(db, targets=targets))
        )
        logger.info(
            "Topic campaign recipients resolved campaign_id=%s recipient_count=%s",
            campaign.id,
            len(recipient_user_ids),
        )
        await create_campaign_audience(
            db,
            campaign_id=campaign.id,
            user_ids=recipient_user_ids,
        )
        logger.info(
            "Audience audit written campaign_id=%s audience_count=%s",
            campaign.id,
            len(recipient_user_ids),
        )
    except Exception:
        logger.exception(
            "Topic campaign audience resolution failed campaign_id=%s",
            campaign.id,
        )

    notification = await create_broadcast_notification(
        db,
        owner_user_id=campaign.created_by_admin_id,
        notification_type_id=campaign.notification_type_id,
        campaign_id=campaign.id,
        title=campaign.title,
        body=campaign.message,
        deep_link_payload={
            _BROADCAST_FLAG_KEY: True,
            "campaign_type": NotificationCampaignType.topic.value,
            "campaign_id": str(campaign.id),
            _FIREBASE_TOPICS_KEY: sorted(firebase_topics),
            _TARGETS_STORAGE_KEY: (campaign.deep_link_payload or {}).get(
                _TARGETS_STORAGE_KEY
            ),
        },
    )
    data_payload = NotificationPayloadBuilder.build(
        notification_type=NotificationCampaignType.topic.value,
        notification_id=notification.id,
        extra=notification.deep_link_payload,
    )
    notification.deep_link_payload = data_payload
    db.add(notification)
    await db.flush()
    logger.info("Broadcast notification created campaign_id=%s", campaign.id)

    # Always send one push per device token. Never publish to multiple Firebase
    # topics for one campaign — a user subscribed to university + major + minor
    # would otherwise receive the same notification N times.
    push_result: dict[str, Any] = {
        "successful_count": 0,
        "failed_count": 0,
        "failed_tokens": [],
    }
    if recipient_user_ids:
        push_eligible_user_ids = await filter_users_eligible_for_push(
            db,
            recipient_user_ids,
            category=NotificationCampaignType.topic.value,
        )
        logger.info(
            "Topic push eligibility campaign_id=%s recipient_count=%s push_eligible_count=%s",
            campaign.id,
            len(recipient_user_ids),
            len(push_eligible_user_ids),
        )
        push_targets = await get_active_push_targets_for_users(
            db, push_eligible_user_ids
        )
        push_result = await _send_token_push(
            campaign, push_targets, data=data_payload
        )
    else:
        logger.warning(
            "Topic push skipped campaign_id=%s reason=no_resolved_recipients "
            "topics=%s (token-only delivery; no Firebase topic fan-out)",
            campaign.id,
            sorted(firebase_topics),
        )
    logger.info(
        "Topic push sent campaign_id=%s successful_count=%s failed_count=%s",
        campaign.id,
        push_result.get("successful_count", 0),
        push_result.get("failed_count", 0),
    )


async def _send_token_push(
    campaign: NotificationCampaign,
    push_targets: list[tuple[str, str | None]],
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not push_targets:
        return {"successful_count": 0, "failed_count": 0, "failed_tokens": []}

    payload = data or {
        "notification_type": (
            campaign.campaign_type.value
            if hasattr(campaign.campaign_type, "value")
            else str(campaign.campaign_type)
        ),
        "campaign_id": str(campaign.id),
    }
    return await send_push_to_devices(
        push_targets,
        campaign.title,
        campaign.message,
        payload,
    )


async def _get_campaign(
    db: AsyncSession,
    campaign_id: UUID,
) -> NotificationCampaign | None:
    return await get_campaign_by_id(db, campaign_id)


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
    items: list[AdminCampaignListItem] = []
    for campaign, recipient_count in rows:
        items.append(
            AdminCampaignListItem(
                id=campaign.id,
                title=campaign.title,
                message=campaign.message,
                campaign_type=campaign.campaign_type,
                status=campaign.status,
                recipient_count=recipient_count,
                targets=await _serialize_stored_targets(
                    campaign.deep_link_payload,
                    db,
                ),
                scheduled_at=campaign.scheduled_at,
                sent_at=campaign.sent_at,
                created_at=campaign.created_at,
            )
        )

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
        "Notification campaigns fetched successfully.",
        data,
        response_cls=AdminCampaignListResponse,
    )
