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
    get_campaign_for_dispatch,
    get_campaigns,
    update_campaign as persist_campaign_update,
    update_campaign_status,
)
from apps.notifications.repositories.campaign_audience_repository import (
    create_campaign_audience,
    get_active_push_targets_grouped_by_user,
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
    CampaignTargetUserValue,
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
from apps.notifications.services.notification_service import (
    get_unread_notification_counts_for_users,
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

NO_ELIGIBLE_USER_MESSAGE = "No eligible users"

# Stored on campaign.deep_link_payload so dispatch_campaign(campaign_id) can run
# later from a worker without a dedicated targets column (no schema change).
_TARGETS_STORAGE_KEY = "targets"
_FIREBASE_TOPICS_KEY = "firebase_topics"
_BROADCAST_FLAG_KEY = "broadcast"


def _campaign_type_value(campaign_type: object) -> str:
    return campaign_type.value if hasattr(campaign_type, "value") else str(campaign_type)


def _campaign_activity_description(action: str, campaign_type: object) -> str:
    raw = _campaign_type_value(campaign_type)
    is_announcement = str(raw).upper() == NotificationCampaignType.announcement.value
    if is_announcement:
        phrases = {
            "create": "sent the announcement",
            "fail": "failed to send the announcement",
            "update": "updated the announcement",
            "delete": "deleted the announcement",
        }
    else:
        phrases = {
            "create": "sent a topic based notification",
            "fail": "failed to send a topic based notification",
            "update": "updated a topic based notification",
            "delete": "deleted a topic based notification",
        }
    return phrases.get(action, "updated a notification campaign")


def _targets_for_resolution(
    targets: list[CreateCampaignTarget],
) -> list[tuple[NotificationTargetType, list[str], bool, bool | None]]:
    return [
        (target.type, list(target.values), bool(target.to_all), target.is_alumni)
        for target in targets
    ]


async def _resolve_payload_recipient_ids(
    db: AsyncSession,
    payload: CreateCampaignRequest,
) -> list[UUID]:
    if payload.campaign_type == NotificationCampaignType.announcement:
        ids = await resolve_announcement_recipients(db)
    else:
        ids = await resolve_topic_recipients(
            db,
            targets=_targets_for_resolution(payload.targets),
        )
    return list(dict.fromkeys(ids))


async def _log_campaign_dispatch_activity(
    db: AsyncSession,
    campaign: NotificationCampaign,
    *,
    status: NotificationCampaignStatus,
    actor_role: str | None = None,
) -> None:
    """Record activity-log / admin-notification after send succeeds or fails."""
    from apps.administration.services.admin_activity_log_service import create_admin_activity_log

    action = "fail" if status == NotificationCampaignStatus.failed else "create"
    await create_admin_activity_log(
        db,
        user_id=campaign.created_by_admin_id,
        role=actor_role or "superadmin",
        action=action,
        module="notification_campaign",
        record_id=campaign.id,
        description=_campaign_activity_description(action, campaign.campaign_type),
        metadata={
            "old": None,
            "new": {
                "campaign_type": _campaign_type_value(campaign.campaign_type),
                "status": status.value if hasattr(status, "value") else str(status),
                "title": campaign.title,
            },
        },
    )


def _stored_target_dict(target: CreateCampaignTarget) -> dict[str, Any]:
    stored: dict[str, Any] = {
        "type": target.type.value,
        "to_all": bool(target.to_all),
        "values": list(target.values),
    }
    if target.is_alumni is not None:
        stored["is_alumni"] = bool(target.is_alumni)
    return stored


def _build_targets_storage(
    targets: list[CreateCampaignTarget],
) -> dict[str, Any]:
    """Persist targets exactly as submitted (POST schema shape)."""
    return {
        _TARGETS_STORAGE_KEY: [_stored_target_dict(target) for target in targets]
    }


def _parse_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return None
    return bool(value)


def _parse_stored_targets(
    payload: dict[str, Any] | None,
) -> list[tuple[NotificationTargetType, list[str], bool, bool | None]]:
    if not payload:
        return []
    raw_targets = payload.get(_TARGETS_STORAGE_KEY) or []
    parsed: list[tuple[NotificationTargetType, list[str], bool, bool | None]] = []
    for item in raw_targets:
        if not isinstance(item, dict):
            continue
        raw_type = item.get("type")
        raw_values = item.get("values") or []
        to_all = bool(item.get("to_all", False))
        is_alumni = _parse_optional_bool(item.get("is_alumni"))
        if not raw_type or not isinstance(raw_values, list):
            continue
        try:
            target_type = NotificationTargetType(raw_type)
        except ValueError:
            logger.warning("Skipping unknown campaign target type: %s", raw_type)
            continue
        values = [str(value).strip() for value in raw_values if value and str(value).strip()]
        if values or to_all:
            parsed.append((target_type, values, to_all, is_alumni))
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


async def _resolve_user_target_values(
    db: AsyncSession,
    values: list[str],
) -> list[CampaignTargetUserValue]:
    """Resolve stored user IDs into id/firstName/lastName objects."""
    if not values:
        return []

    from apps.profiles.db_models import Profile

    user_ids = [uid for uid in (_parse_uuid(v) for v in values) if uid is not None]
    id_to_user: dict[str, CampaignTargetUserValue] = {}
    if user_ids:
        rows = (
            await db.execute(
                select(Profile.user_id, Profile.first_name, Profile.last_name).where(
                    Profile.user_id.in_(user_ids)
                )
            )
        ).all()
        id_to_user = {
            str(user_id).lower(): CampaignTargetUserValue(
                id=str(user_id),
                firstName=(first_name or "").strip(),
                lastName=(last_name or "").strip(),
            )
            for user_id, first_name, last_name in rows
        }

    resolved: list[CampaignTargetUserValue] = []
    for value in values:
        key = str(value).strip().lower()
        existing = id_to_user.get(key)
        if existing is not None:
            resolved.append(existing)
            continue
        parsed = _parse_uuid(value)
        resolved.append(
            CampaignTargetUserValue(
                id=str(parsed) if parsed is not None else str(value).strip(),
                firstName="",
                lastName="",
            )
        )
    return resolved


async def _serialize_stored_targets(
    payload: dict[str, Any] | None,
    db: AsyncSession,
) -> list[CampaignTargetResponse]:
    """
    Return stored campaign targets with display enrichment.

    USERS values become ``{id, firstName, lastName}`` objects; other types
    remain strings (IDs resolved to labels when possible).
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
        to_all = bool(item.get("to_all", False))
        if raw_type is None or (raw_values is not None and not isinstance(raw_values, list)):
            continue
        try:
            target_type = NotificationTargetType(raw_type)
        except ValueError:
            logger.warning("Skipping unknown stored campaign target type: %s", raw_type)
            continue

        raw_list = [str(value) for value in (raw_values or []) if value and str(value).strip()]
        if not raw_list:
            resolved_values: list[str | CampaignTargetUserValue] = []
        elif target_type == NotificationTargetType.users:
            resolved_values = await _resolve_user_target_values(db, raw_list)
        else:
            resolved_values = await _resolve_target_values(
                db,
                target_type=target_type,
                values=raw_list,
            )
        serialized.append(
            CampaignTargetResponse(
                type=target_type,
                to_all=to_all,
                values=resolved_values,
                is_alumni=_parse_optional_bool(item.get("is_alumni")),
            )
        )
    return serialized


async def create_campaign(
    db: AsyncSession,
    *,
    admin_user_id: UUID,
    payload: CreateCampaignRequest,
    actor_role: str | None = None,
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

    if not await _resolve_payload_recipient_ids(db, payload):
        return error_response(
            NO_ELIGIBLE_USER_MESSAGE,
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
    actor_user_id: UUID | None = None,
    actor_role: str | None = None,
) -> UpdateCampaignResponse:
    """Update an active campaign. Does not re-dispatch push notifications."""
    campaign = await get_campaign_by_id(db, payload.id, active_only=True)
    if campaign is None:
        return error_response(
            "Notification campaign not found.",
            response_cls=UpdateCampaignResponse,
        )

    old_state = {
        "campaign_type": campaign.campaign_type.value
        if hasattr(campaign.campaign_type, "value")
        else str(campaign.campaign_type),
        "title": campaign.title,
        "status": campaign.status.value if hasattr(campaign.status, "value") else str(campaign.status),
        "is_active": bool(campaign.is_active),
    }

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
        from apps.administration.services.admin_activity_log_service import create_admin_activity_log

        await create_admin_activity_log(
            db,
            user_id=actor_user_id or campaign.created_by_admin_id,
            role=actor_role,
            action="update",
            module="notification_campaign",
            record_id=campaign.id,
            description=_campaign_activity_description("update", payload.campaign_type),
            metadata={
                "old": old_state,
                "new": {
                    "campaign_type": payload.campaign_type.value
                    if hasattr(payload.campaign_type, "value")
                    else str(payload.campaign_type),
                    "title": title,
                    "status": campaign.status.value if hasattr(campaign.status, "value") else str(campaign.status),
                    "is_active": bool(campaign.is_active),
                },
            },
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
    actor_user_id: UUID | None = None,
    actor_role: str | None = None,
) -> DeleteCampaignResponse:
    """Soft-delete a campaign by setting is_active=false."""
    campaign = await get_campaign_by_id(db, campaign_id)
    if campaign is None or not campaign.is_active:
        return error_response(
            "Notification campaign not found.",
            response_cls=DeleteCampaignResponse,
        )

    try:
        old_state = {
            "is_active": True,
            "status": campaign.status.value if hasattr(campaign.status, "value") else str(campaign.status),
            "title": campaign.title,
        }
        await deactivate_campaign(db, campaign)
        from apps.administration.services.admin_activity_log_service import create_admin_activity_log

        await create_admin_activity_log(
            db,
            user_id=actor_user_id or campaign.created_by_admin_id,
            role=actor_role,
            action="delete",
            module="notification_campaign",
            record_id=campaign.id,
            description=_campaign_activity_description("delete", campaign.campaign_type),
            metadata={
                "old": old_state,
                "new": {
                    "is_active": False,
                    "status": campaign.status.value if hasattr(campaign.status, "value") else str(campaign.status),
                },
            },
        )
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
    *,
    actor_role: str | None = None,
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

    campaign = await get_campaign_for_dispatch(db, campaign_id)
    if campaign is None:
        logger.error("Campaign failed campaign_id=%s reason=not_found", campaign_id)
        raise ValueError("Notification campaign not found.")

    if campaign.status != NotificationCampaignStatus.draft:
        logger.info(
            "Campaign dispatch skipped campaign_id=%s status=%s",
            campaign_id,
            campaign.status.value
            if hasattr(campaign.status, "value")
            else str(campaign.status),
        )
        return

    existing_notification = await get_notification_by_campaign_id(db, campaign_id)
    if existing_notification is not None:
        logger.info(
            "Campaign dispatch skipped campaign_id=%s reason=already_dispatched",
            campaign_id,
        )
        await _update_campaign_status(
            db,
            campaign,
            status=NotificationCampaignStatus.sent,
            sent_at=campaign.sent_at or utc_now(),
        )
        await db.commit()
        return

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
        await _log_campaign_dispatch_activity(
            db,
            campaign,
            status=NotificationCampaignStatus.sent,
            actor_role=actor_role,
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
                await _log_campaign_dispatch_activity(
                    db,
                    campaign,
                    status=NotificationCampaignStatus.failed,
                    actor_role=actor_role,
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
    if not recipient_user_ids:
        raise ValueError(NO_ELIGIBLE_USER_MESSAGE)

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

    # Persist audience + broadcast before push so badges cannot outlive a rollback.
    await db.commit()

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
    push_result = await _send_token_push(
        db,
        campaign,
        push_eligible_user_ids,
        data=data_payload,
    )
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

    if not recipient_user_ids:
        raise ValueError(NO_ELIGIBLE_USER_MESSAGE)

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

    # Persist audience + broadcast before push so badges cannot outlive a rollback.
    await db.commit()

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
        push_result = await _send_token_push(
            db,
            campaign,
            push_eligible_user_ids,
            data=data_payload,
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
    db: AsyncSession,
    campaign: NotificationCampaign,
    user_ids: list[UUID],
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Fan out campaign pushes per recipient user with that user's unread badge.

    Never computes one global unread count for the whole campaign.
    """
    if not user_ids:
        return {"successful_count": 0, "failed_count": 0, "failed_tokens": []}

    targets_by_user = await get_active_push_targets_grouped_by_user(db, user_ids)
    if not targets_by_user:
        return {"successful_count": 0, "failed_count": 0, "failed_tokens": []}

    unread_by_user = await get_unread_notification_counts_for_users(
        db,
        list(targets_by_user.keys()),
    )

    raw_payload = data or {
        "notification_type": (
            campaign.campaign_type.value
            if hasattr(campaign.campaign_type, "value")
            else str(campaign.campaign_type)
        ),
        "campaign_id": str(campaign.id),
    }

    successful_count = 0
    failed_count = 0
    failed_tokens: list[str] = []

    for user_id, push_targets in targets_by_user.items():
        unread_count = int(unread_by_user.get(user_id, 0))
        user_payload = dict(raw_payload)
        user_payload["unread_count"] = unread_count
        payload = NotificationPayloadBuilder.for_fcm(user_payload)
        result = await send_push_to_devices(
            push_targets,
            campaign.title,
            campaign.message,
            payload,
            badge=unread_count,
        )
        successful_count += int(result.get("successful_count", 0))
        failed_count += int(result.get("failed_count", 0))
        failed_tokens.extend(result.get("failed_tokens") or [])
        logger.info(
            "Campaign push user scoped campaign_id=%s user_id=%s unread_count=%s "
            "successful=%s failed=%s",
            campaign.id,
            user_id,
            unread_count,
            result.get("successful_count", 0),
            result.get("failed_count", 0),
        )

    return {
        "successful_count": successful_count,
        "failed_count": failed_count,
        "failed_tokens": failed_tokens,
    }


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
