from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.notifications.schemas import (
    AdminCampaignListResponse,
    CreateCampaignRequest,
    CreateCampaignResponse,
    DeleteCampaignRequest,
    DeleteCampaignResponse,
    MarkAllNotificationsReadResponse,
    MarkNotificationReadResponse,
    NotificationListResponse,
    NotificationPreferencesResponse,
    UpdateCampaignRequest,
    UpdateCampaignResponse,
    UpdateNotificationPreferencesRequest,
)
from apps.notifications.services import (
    create_campaign,
    delete_campaign,
    dispatch_campaign,
    get_preferences,
    list_campaigns,
    list_notifications,
    mark_all_read,
    mark_as_read,
    update_campaign,
    update_preferences,
)
from common.enums import NotificationCampaignStatus, NotificationCampaignType
from core.database.session import get_session
from core.security.auth import get_current_admin, get_current_app_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["9] Notifications"])


@router.get(
    "/notifications/preferences",
    response_model=NotificationPreferencesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get notification preferences",
    description="Return the authenticated user's push/in-app/category notification preferences.",
)
async def get_notification_preferences_route(
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationPreferencesResponse:
    return await get_preferences(db, user_id=current_user.id)


@router.patch(
    "/notifications/preferences",
    response_model=NotificationPreferencesResponse,
    status_code=status.HTTP_200_OK,
    summary="Update notification preferences",
    description=(
        "Partial update of notification preferences. "
        "Only supplied fields are changed; unspecified fields are left unchanged."
    ),
)
async def update_notification_preferences_route(
    payload: UpdateNotificationPreferencesRequest,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> NotificationPreferencesResponse:
    return await update_preferences(db, user_id=current_user.id, payload=payload)


@router.get(
    "/notifications",
    response_model=NotificationListResponse,
    status_code=status.HTTP_200_OK,
    summary="List my notifications",
    description=(
        "Return in-app notifications for the authenticated user, newest first. "
        "When both page and pageSize are omitted, all notifications are returned. "
        "Use unread_only=true to filter unread items."
    ),
)
async def list_my_notifications_route(
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    unread_only: bool = Query(default=False),
) -> NotificationListResponse:
    return await list_notifications(
        db,
        user_id=current_user.id,
        page=page,
        page_size=pageSize,
        unread_only=unread_only,
    )


@router.patch(
    "/notifications/read-all",
    response_model=MarkAllNotificationsReadResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark all notifications as read",
    description="Mark every unread notification for the authenticated user as read.",
)
async def mark_all_notifications_read_route(
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MarkAllNotificationsReadResponse:
    return await mark_all_read(db, user_id=current_user.id)


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=MarkNotificationReadResponse,
    status_code=status.HTTP_200_OK,
    summary="Mark a notification as read",
    description="Mark a single notification as read for the authenticated user.",
)
async def mark_notification_read_route(
    notification_id: UUID,
    current_user: Annotated[User, Depends(get_current_app_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> MarkNotificationReadResponse:
    return await mark_as_read(
        db,
        user_id=current_user.id,
        notification_id=notification_id,
    )


@router.get(
    "/admin/notifications",
    response_model=AdminCampaignListResponse,
    status_code=status.HTTP_200_OK,
    summary="List admin notification campaigns",
    description=(
        "Return notification campaigns created by admins. "
        "Supports optional search, campaign_type/status filters, and pagination. "
        "When both page and pageSize are omitted, all matching campaigns are returned."
    ),
)
async def admin_list_notification_campaigns(
    current_user: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    search: str | None = Query(default=None, description="Search title and message"),
    campaign_type: NotificationCampaignType | None = Query(default=None),
    status_filter: NotificationCampaignStatus | None = Query(
        default=None,
        alias="status",
        description="Filter by campaign status",
    ),
) -> AdminCampaignListResponse:
    _ = current_user
    return await list_campaigns(
        db,
        page=page,
        page_size=pageSize,
        search=search,
        campaign_type=campaign_type,
        status=status_filter,
    )

# admin

@router.post(
    "/admin/notifications",
    response_model=CreateCampaignResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a notification campaign",
    description=(
        "Create an immediate ANNOUNCEMENT or TOPIC campaign, then dispatch processing "
        "(audience, in-app notifications, and Firebase push). "
        "ANNOUNCEMENT: omit targets (or send []). "
        "TOPIC: provide targets as a list of {type, values} filters "
        "(UNIVERSITY, MAJOR, MINOR, EDUCATION_LEVEL, COUNTRY, INTERESTS, HASHTAGS)."
    ),
)
async def admin_create_notification_campaign(
    payload: CreateCampaignRequest,
    current_user: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CreateCampaignResponse:
    result = await create_campaign(
        db,
        admin_user_id=current_user.id,
        payload=payload,
    )
    if result.status and result.data is not None:
        try:
            await dispatch_campaign(db, result.data.id)
        except Exception:
            logger.exception(
                "dispatch_campaign failed for campaign_id=%s",
                result.data.id,
            )
    return result


@router.patch(
    "/admin/notifications",
    response_model=UpdateCampaignResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a notification campaign",
    description=(
        "Update an active admin notification campaign. "
        "Request body matches POST with an additional id field. "
        "Does not re-dispatch push notifications."
    ),
)
async def admin_update_notification_campaign(
    payload: UpdateCampaignRequest,
    current_user: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> UpdateCampaignResponse:
    _ = current_user
    return await update_campaign(db, payload=payload)


@router.delete(
    "/admin/notifications",
    response_model=DeleteCampaignResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a notification campaign",
    description=(
        "Soft-delete a notification campaign by setting is_active=false. "
        "Provide the campaign id in the JSON body."
    ),
)
async def admin_delete_notification_campaign(
    payload: DeleteCampaignRequest,
    current_user: Annotated[User, Depends(get_current_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> DeleteCampaignResponse:
    _ = current_user
    return await delete_campaign(db, campaign_id=payload.id)
