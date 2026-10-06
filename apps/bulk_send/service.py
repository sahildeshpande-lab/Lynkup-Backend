from __future__ import annotations

import logging
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.profiles.db_models import Profile
from apps.profiles.db_models.country_db_model import Country
from apps.bulk_send.enums import BulkEmailTargetType, EmailCampaignStatus, EmailDeliveryStatus
from apps.bulk_send.models import EmailCampaign, EmailDelivery, utc_now
from apps.bulk_send.recipient_resolution import (
    bulk_email_preference_opt_out_message,
    load_users_by_ids_preserving_order,
    resolve_bulk_email_audience,
)
from apps.bulk_send.repository import (
    delivery_stats_for_campaign,
    get_campaign,
    get_campaign_deliveries_with_profiles,
    list_campaigns,
)
from apps.bulk_send.schemas import (
    AttachmentMeta,
    AttachmentUploadData,
    BulkEmailTarget,
    CampaignDetail,
    CampaignRecipientDetail,
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
from common.pagination import build_paginated_response, paginate_or_all

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

        await self._validate_country_targets(db, payload.targets)

        audience = await resolve_bulk_email_audience(
            db,
            targets=payload.targets,
            is_alumni=payload.is_alumni,
        )
        users = await load_users_by_ids_preserving_order(db, audience.eligible_user_ids)
        if not users:
            if (
                not audience.eligible_user_ids
                and audience.preference_excluded_user_ids
            ):
                raise ApiError(
                    await self._bulk_email_opt_out_error(
                        db,
                        audience.preference_excluded_user_ids,
                    )
                )
            raise ApiError("No eligible recipients found to send bulk email")

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
        search: str | None = None,
    ) -> dict:
        items, total = await list_campaigns(
            db, page=page, page_size=page_size, search=search
        )
        summaries = [CampaignSummary.model_validate(item) for item in items]
        return build_paginated_response(summaries, page, page_size, total).model_dump(mode="json")

    async def get_campaign_detail(
        self,
        *,
        db: AsyncSession,
        campaign_id: UUID,
        page: int | None = None,
        page_size: int | None = None,
        search: str | None = None,
    ) -> CampaignDetail:
        campaign = await get_campaign(db, campaign_id)
        if campaign is None:
            raise ApiError("Campaign not found")
        stats = await delivery_stats_for_campaign(db, campaign_id)
        delivery_rows, total = await get_campaign_deliveries_with_profiles(
            db,
            campaign_id,
            search=search,
            page=page,
            page_size=page_size,
        )

        recipients: list[CampaignRecipientDetail] = []
        for delivery, profile in delivery_rows:
            first_name = profile.first_name if profile else None
            last_name = profile.last_name if profile else None
            parts = [p for p in (first_name, last_name) if p]
            user_name = " ".join(parts).strip() or None
            is_delivered = (
                delivery.status == EmailDeliveryStatus.sent
                or delivery.delivered_at is not None
            )
            recipients.append(
                CampaignRecipientDetail(
                    user_id=delivery.user_id,
                    first_name=first_name,
                    last_name=last_name,
                    user_name=user_name,
                    email=delivery.email,
                    status=delivery.status,
                    is_delivered=is_delivered,
                    delivered_at=delivery.delivered_at,
                    last_attempt_at=delivery.last_attempt_at,
                    failure_reason=delivery.failure_reason,
                )
            )

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
            recipients=paginate_or_all(
                recipients,
                page=page,
                page_size=page_size,
                total_items=total,
            ),
        )

    async def _validate_country_targets(
        self,
        db: AsyncSession,
        targets: list[BulkEmailTarget],
    ) -> None:
        country_ids: list[UUID] = []
        for target in targets:
            if target.type != BulkEmailTargetType.COUNTRY or target.to_all:
                continue
            for value in target.values:
                try:
                    country_ids.append(UUID(value))
                except (TypeError, ValueError):
                    continue
        if not country_ids:
            return
        await self._validate_countries(db, country_ids)

    async def _validate_countries(self, db: AsyncSession, country_ids: list[UUID]) -> None:
        rows = (
            await db.execute(
                select(Country.id).where(
                    Country.id.in_(country_ids),
                    Country.is_active.is_(True),
                )
            )
        ).scalars().all()
        found = set(rows)
        missing = [str(country_id) for country_id in country_ids if country_id not in found]
        if missing:
            raise ApiError(f"Invalid or inactive country_ids: {', '.join(missing)}")

    async def _bulk_email_opt_out_error(
        self,
        db: AsyncSession,
        excluded_user_ids: list[UUID],
    ) -> str:
        email: str | None = None
        if len(excluded_user_ids) == 1:
            opted_out_users = await load_users_by_ids_preserving_order(
                db,
                excluded_user_ids,
            )
            if opted_out_users:
                email = opted_out_users[0].email
        return bulk_email_preference_opt_out_message(
            excluded_count=len(excluded_user_ids),
            email=email,
        )

    def _eligible_users_stmt(
        self,
        *,
        country_ids: list[UUID] | None = None,
        is_alumni: bool = False,
    ):
        """Build a base eligible-users select (kept for unit tests / diagnostics)."""
        from common.enums import UserStatus

        stmt = (
            select(User)
            .where(User.is_deleted.is_(False))
            .where(User.deleted_at.is_(None))
            .where(
                User.status.notin_(
                    [UserStatus.suspended, UserStatus.banned, UserStatus.deleting]
                )
            )
            .where(User.email.is_not(None))
            .where(User.email != "")
        )
        if country_ids or is_alumni:
            stmt = stmt.join(Profile, Profile.user_id == User.id)
        if country_ids:
            stmt = stmt.where(Profile.country_id.in_(country_ids))
        if is_alumni:
            stmt = stmt.where(Profile.is_alumni.is_(True))
        return stmt.order_by(User.created_at.asc())

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
