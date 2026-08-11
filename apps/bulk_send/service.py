from __future__ import annotations

import logging
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.bulk_send.enums import EmailCampaignStatus, EmailDeliveryStatus
from apps.bulk_send.models import EmailCampaign, EmailDelivery, utc_now
from apps.bulk_send.repository import (
    delivery_stats_for_campaign,
    get_campaign,
    list_campaigns,
)
from apps.bulk_send.schemas import (
    AttachmentMeta,
    AttachmentUploadData,
    CampaignDetail,
    CampaignSummary,
    CreateBulkCampaignRequest,
    CreateCampaignData,
    DeliveryStats,
)
from apps.bulk_send.storage import (
    MAX_ATTACHMENTS_PER_CAMPAIGN,
    BulkSendStorage,
    get_bulk_send_storage,
    upload_attachment_file,
)
from common.exceptions import ApiError
from common.pagination import build_paginated_response

logger = logging.getLogger(__name__)


class BulkSendService:
    def __init__(self, storage: BulkSendStorage | None = None) -> None:
        self.storage = storage or get_bulk_send_storage()

    async def upload_attachment(
        self,
        *,
        admin: User,
        file: UploadFile,
    ) -> AttachmentUploadData:
        meta = await upload_attachment_file(
            file=file,
            admin_id=admin.id,
            storage=self.storage,
        )
        return AttachmentUploadData(**meta)

    async def create_campaign(
        self,
        *,
        admin: User,
        payload: CreateBulkCampaignRequest,
        db: AsyncSession,
    ) -> CreateCampaignData:
        if len(payload.attachments) > MAX_ATTACHMENTS_PER_CAMPAIGN:
            raise ApiError(
                f"A campaign may include at most {MAX_ATTACHMENTS_PER_CAMPAIGN} attachments"
            )

        users = await self._load_recipients(db, payload.user_ids)
        attachments = self._validated_attachments(admin.id, payload.attachments)

        now = utc_now()
        campaign = EmailCampaign(
            name=payload.name.strip(),
            subject=payload.subject.strip(),
            body_html=payload.body_html,
            body_text=payload.body_text,
            status=EmailCampaignStatus.queued,
            created_by=admin.id,
            total_recipients=len(users),
            attachments=[item.model_dump() for item in attachments],
            created_at=now,
            updated_at=now,
        )
        db.add(campaign)
        await db.flush()

        for user in users:
            db.add(
                EmailDelivery(
                    campaign_id=campaign.id,
                    user_id=user.id,
                    email=user.email,
                    status=EmailDeliveryStatus.pending,
                    attempt_count=0,
                    metadata_={},
                    created_at=now,
                    updated_at=now,
                )
            )

        await db.commit()
        await db.refresh(campaign)
        logger.info(
            "Created bulk email campaign_id=%s recipients=%s admin_id=%s",
            campaign.id,
            campaign.total_recipients,
            admin.id,
        )
        return CreateCampaignData(
            campaign_id=campaign.id,
            status=campaign.status,
            total_recipients=campaign.total_recipients,
        )

    async def list_campaigns_page(
        self,
        *,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        items, total = await list_campaigns(db, page=page, page_size=page_size)
        summaries = [CampaignSummary.model_validate(item) for item in items]
        return build_paginated_response(summaries, page, page_size, total).model_dump(mode="json")

    async def get_campaign_detail(
        self,
        *,
        db: AsyncSession,
        campaign_id: UUID,
    ) -> CampaignDetail:
        campaign = await get_campaign(db, campaign_id)
        if campaign is None:
            raise ApiError("Campaign not found")
        stats = await delivery_stats_for_campaign(db, campaign_id)
        return CampaignDetail(
            id=campaign.id,
            name=campaign.name,
            subject=campaign.subject,
            status=campaign.status,
            created_by=campaign.created_by,
            total_recipients=campaign.total_recipients,
            started_at=campaign.started_at,
            completed_at=campaign.completed_at,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
            body_html=campaign.body_html,
            body_text=campaign.body_text,
            attachments=list(campaign.attachments or []),
            delivery_stats=stats or DeliveryStats(),
        )

    async def _load_recipients(self, db: AsyncSession, user_ids: list[UUID]) -> list[User]:
        stmt = select(User).where(User.id.in_(user_ids)).where(User.is_deleted.is_(False))
        users = list((await db.execute(stmt)).scalars().all())
        found = {user.id for user in users}
        missing = [str(uid) for uid in user_ids if uid not in found]
        if missing:
            raise ApiError(f"Invalid recipient user_ids: {', '.join(missing)}")

        without_email = [str(user.id) for user in users if not (user.email or "").strip()]
        if without_email:
            raise ApiError(f"Recipients missing email addresses: {', '.join(without_email)}")

        # Preserve request order
        by_id = {user.id: user for user in users}
        return [by_id[uid] for uid in user_ids]

    def _validated_attachments(
        self,
        admin_id: UUID,
        attachments: list[AttachmentMeta],
    ) -> list[AttachmentMeta]:
        validated: list[AttachmentMeta] = []
        for item in attachments:
            key = item.storage_key.replace("\\", "/").lstrip("/")
            if ".." in key.split("/"):
                raise ApiError(f"Invalid attachment storage_key: {item.storage_key}")
            expected_prefix = f"email-campaigns/tmp/{admin_id}/"
            if not key.startswith(expected_prefix):
                raise ApiError(
                    "Attachment storage_key must belong to the current admin upload namespace"
                )
            if not self.storage.exists(key):
                raise ApiError(f"Attachment not found in storage: {item.file_name}")
            validated.append(
                AttachmentMeta(
                    file_name=item.file_name,
                    storage_key=key,
                    content_type=item.content_type,
                )
            )
        return validated


def get_bulk_send_service() -> BulkSendService:
    return BulkSendService()
