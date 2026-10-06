"""Pending connection-request reminder producer.

Sends in-app and push notifications for users with pending connection requests
waiting longer than the reminder cadence (default 7 days).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.connections.db_models import ConnectionRequest
from apps.notifications.services import notify_connection_reminder
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from common.user_visibility import visible_user_filters
from core.database.session import async_session_factory
from core.images import generate_profile_image_url

logger = logging.getLogger(__name__)

REMINDER_INTERVAL = timedelta(days=7)
DEFAULT_BATCH_LIMIT = 50


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class PendingReminderSender:
    """Sender snapshot for pending connection reminders."""

    request_id: UUID
    sender_user_id: UUID
    first_name: str | None
    last_name: str | None
    edu_level: str | None
    university_name: str | None
    major: str | None
    profile_photo_url: str | None


class ConnectionReminderService:
    """Business logic for pending connection-request reminders."""

    def __init__(
        self,
        *,
        reminder_interval: timedelta = REMINDER_INTERVAL,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        session_factory=None,
    ) -> None:
        self._reminder_interval = reminder_interval
        self._batch_limit = batch_limit
        self._session_factory = session_factory

    def _sessions(self):
        return self._session_factory or async_session_factory

    def _cutoff(self, now: datetime | None = None) -> datetime:
        return (now or utc_now()) - self._reminder_interval

    async def process_pending_reminders(self, limit: int | None = None) -> int:
        """Find eligible receivers and dispatch push reminders."""
        batch_limit = self._batch_limit if limit is None else max(1, limit)
        cutoff = self._cutoff()

        session_factory = self._sessions()
        async with session_factory() as session:
            receiver_ids = await self._find_eligible_receiver_ids(
                session, cutoff=cutoff, limit=batch_limit
            )

        if not receiver_ids:
            logger.info("Connection reminder producer: no eligible receivers.")
            return 0

        dispatched = 0
        for receiver_id in receiver_ids:
            try:
                if await self._send_reminder_for_receiver(receiver_id, cutoff=cutoff):
                    dispatched += 1
            except Exception:
                logger.exception(
                    "Connection reminder producer failed for receiver=%s",
                    receiver_id,
                )
        logger.info(
            "Connection reminder producer finished eligible=%s dispatched=%s",
            len(receiver_ids),
            dispatched,
        )
        return dispatched

    async def _find_eligible_receiver_ids(
        self,
        session: AsyncSession,
        *,
        cutoff: datetime,
        limit: int,
    ) -> list[UUID]:
        """Efficiently identify receivers with pending inbound requests due for a reminder."""
        ReceiverUser = User
        stmt = (
            select(ConnectionRequest.receiver_user_id)
            .join(Profile, Profile.user_id == ConnectionRequest.receiver_user_id)
            .join(ReceiverUser, ReceiverUser.id == ConnectionRequest.receiver_user_id)
            .where(
                ConnectionRequest.status == "pending",
                or_(
                    Profile.connection_reminder_sent_at.is_(None),
                    Profile.connection_reminder_sent_at <= cutoff,
                ),
                *visible_user_filters(ReceiverUser),
            )
            .group_by(ConnectionRequest.receiver_user_id)
            .order_by(func.min(ConnectionRequest.created_at).asc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        return [row[0] for row in rows]

    async def _send_reminder_for_receiver(
        self,
        receiver_user_id: UUID,
        *,
        cutoff: datetime,
    ) -> bool:
        """Lock profile, re-check eligibility, send push notification, stamp cadence atomically."""
        session_factory = self._sessions()
        async with session_factory() as session:
            profile = await self._lock_profile(session, receiver_user_id)
            if profile is None:
                return False

            if not self._is_cadence_due(profile.connection_reminder_sent_at, cutoff):
                return False

            senders = await self._load_pending_senders(session, receiver_user_id)
            if not senders:
                return False

            sender_name = senders[0].first_name if len(senders) == 1 else None
            sender_id = senders[0].sender_user_id if len(senders) == 1 else None

            from apps.notifications.repositories.notification_repository import (
                get_preferences_by_user_id,
            )
            from apps.notifications.services.notification_service import (
                is_weekly_lynkup_reminder_enabled,
            )

            preference = await get_preferences_by_user_id(session, receiver_user_id)
            weekly_enabled = is_weekly_lynkup_reminder_enabled(
                getattr(preference, "category_preferences", None) if preference else None,
                email_preferences=(
                    getattr(preference, "email_preferences", None) if preference else None
                ),
            )

            if not weekly_enabled:
                logger.info(
                    "Connection reminder skipped receiver=%s "
                    "reason=weekly_lynkup_request_reminder_disabled",
                    receiver_user_id,
                )
                # Advance cadence so opted-out users are not retried every tick.
                if not await self._stamp_reminder_sent(
                    session, profile_id=profile.id, cutoff=cutoff
                ):
                    return False
                await session.commit()
                return True

            notification = await notify_connection_reminder(
                session,
                recipient_user_id=receiver_user_id,
                pending_count=len(senders),
                sender_name=sender_name,
                sender_user_id=sender_id,
                send_push=True,
            )
            if notification is None:
                # Delivery failed (missing type, etc.). Do not stamp cadence so
                # the next cron tick can retry.
                await session.rollback()
                logger.warning(
                    "Connection reminder not delivered receiver=%s pending=%s",
                    receiver_user_id,
                    len(senders),
                )
                return False

            if not await self._stamp_reminder_sent(
                session, profile_id=profile.id, cutoff=cutoff
            ):
                return False

            await session.commit()
            logger.info(
                "Dispatched connection reminder notification receiver=%s pending=%s notification_id=%s",
                receiver_user_id,
                len(senders),
                getattr(notification, "id", None),
            )
            return True

    async def _stamp_reminder_sent(
        self,
        session: AsyncSession,
        *,
        profile_id: UUID,
        cutoff: datetime,
    ) -> bool:
        """Atomically stamp connection_reminder_sent_at. Returns False if race lost."""
        now = utc_now()
        result = await session.execute(
            update(Profile)
            .where(Profile.id == profile_id)
            .where(
                or_(
                    Profile.connection_reminder_sent_at.is_(None),
                    Profile.connection_reminder_sent_at <= cutoff,
                )
            )
            .values(
                connection_reminder_sent_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            await session.rollback()
            return False
        return True

    async def _lock_profile(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> Profile | None:
        stmt = select(Profile).where(Profile.user_id == user_id).with_for_update()
        try:
            return (await session.execute(stmt)).scalars().first()
        except Exception:
            # SQLite and some dialects may reject FOR UPDATE; fall back unlocked.
            await session.rollback()
            return (
                await session.execute(select(Profile).where(Profile.user_id == user_id))
            ).scalars().first()

    @staticmethod
    def _is_cadence_due(
        connection_reminder_sent_at: datetime | None,
        cutoff: datetime,
    ) -> bool:
        if connection_reminder_sent_at is None:
            return True
        sent_at = connection_reminder_sent_at
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        return sent_at <= cutoff

    async def _load_pending_senders(
        self,
        session: AsyncSession,
        receiver_user_id: UUID,
    ) -> list[PendingReminderSender]:
        SenderUser = User
        stmt = (
            select(ConnectionRequest, Profile, University.name)
            .join(Profile, Profile.user_id == ConnectionRequest.sender_user_id)
            .join(SenderUser, SenderUser.id == ConnectionRequest.sender_user_id)
            .outerjoin(University, University.id == Profile.university_id)
            .where(
                ConnectionRequest.receiver_user_id == receiver_user_id,
                ConnectionRequest.status == "pending",
                *visible_user_filters(SenderUser),
            )
            .order_by(ConnectionRequest.created_at.asc())
        )
        rows = (await session.execute(stmt)).all()
        items: list[PendingReminderSender] = []
        for req, sender_profile, university_name in rows:
            photo = None
            if sender_profile.profile_photo_url:
                try:
                    photo = generate_profile_image_url(sender_profile.profile_photo_url)
                except Exception:
                    photo = sender_profile.profile_photo_url
            items.append(
                PendingReminderSender(
                    request_id=req.id,
                    sender_user_id=req.sender_user_id,
                    first_name=sender_profile.first_name,
                    last_name=sender_profile.last_name,
                    edu_level=sender_profile.edu_level,
                    university_name=university_name,
                    major=sender_profile.major,
                    profile_photo_url=photo,
                )
            )
        return items


async def process_connection_reminders(
    limit: int | None = None, *, session_factory=None
) -> int:
    """Entrypoint for the background scheduler loop (producer only)."""
    return await ConnectionReminderService(
        session_factory=session_factory
    ).process_pending_reminders(limit=limit)
