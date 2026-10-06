"""Graduation completion email producer.

Queues fully rendered emails into ``transactional_email_log`` for the existing
email cron to deliver. Does not call SendGrid directly.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from apps.profiles.services.response_service import is_graduation_completed
from common.user_visibility import visible_user_filters
from core.database.session import async_session_factory
from core.email_service import (
    GRADUATION_COMPLETION_PURPOSE,
    _render_graduation_completion_email,
    queue_email_on_session,
)

logger = logging.getLogger(__name__)

DEFAULT_BATCH_LIMIT = 50


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def reference_date_today() -> date:
    return utc_now().date()


class GraduationEmailService:
    """Business logic for one-time graduation completion emails."""

    def __init__(self, *, batch_limit: int = DEFAULT_BATCH_LIMIT, session_factory=None) -> None:
        self._batch_limit = batch_limit
        self._session_factory = session_factory

    def _sessions(self):
        """Use the injected factory; FastAPI callers keep the global factory."""
        return self._session_factory or async_session_factory

    async def process_graduation_completion_emails(self, limit: int | None = None) -> int:
        """Find eligible graduates and queue at most ``limit`` completion emails."""
        batch_limit = self._batch_limit if limit is None else max(1, limit)
        today = reference_date_today()

        session_factory = self._sessions()
        async with session_factory() as session:
            await self._mark_completed_graduates_as_alumni(session, today=today)
            user_ids = await self._find_eligible_user_ids(session, today=today, limit=batch_limit)

        if not user_ids:
            logger.info("Graduation email producer: no eligible users.")
            return 0

        queued = 0
        for user_id in user_ids:
            try:
                if await self._queue_graduation_email_for_user(user_id, today=today):
                    queued += 1
            except Exception:
                logger.exception(
                    "Graduation email producer failed for user=%s",
                    user_id,
                )

        logger.info(
            "Graduation email producer finished eligible=%s queued=%s",
            len(user_ids),
            queued,
        )
        return queued

    async def _mark_completed_graduates_as_alumni(
        self,
        session: AsyncSession,
        *,
        today: date,
    ) -> int:
        """Mark profiles with completed graduation as alumni without redundant updates."""
        now = utc_now()
        result = await session.execute(
            update(Profile)
            .where(Profile.graduation_date.is_not(None))
            .where(Profile.graduation_date < today)
            .where(or_(Profile.is_alumni.is_(False), Profile.is_alumni.is_(None)))
            .values(is_alumni=True, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        updated = int(result.rowcount or 0)
        if updated:
            await session.commit()
            logger.info("Marked %s completed graduate(s) as alumni", updated)
        return updated

    async def _find_eligible_user_ids(
        self,
        session: AsyncSession,
        *,
        today: date,
        limit: int,
    ) -> list[UUID]:
        stmt = (
            select(Profile.user_id)
            .join(User, User.id == Profile.user_id)
            .where(
                Profile.graduation_date.is_not(None),
                Profile.graduation_date < today,
                Profile.graduation_completion_email_sent_at.is_(None),
                User.is_deleted.is_(False),
                *visible_user_filters(User),
            )
            .order_by(Profile.graduation_date.asc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        return [row[0] for row in rows]

    async def _queue_graduation_email_for_user(
        self,
        user_id: UUID,
        *,
        today: date,
    ) -> bool:
        """Lock profile, re-check eligibility, queue email, stamp sent-at atomically."""
        session_factory = self._sessions()
        async with session_factory() as session:
            profile = await self._lock_profile(session, user_id)
            if profile is None:
                return False

            if not self._is_eligible(profile, today=today):
                return False

            user_row = (
                await session.execute(
                    select(User.email).where(User.id == user_id)
                )
            ).first()
            if user_row is None or not user_row[0]:
                return False
            recipient_email = str(user_row[0])

            university_name = None
            if profile.university_id is not None:
                university_name = (
                    await session.execute(
                        select(University.name).where(University.id == profile.university_id)
                    )
                ).scalar_one_or_none()

            subject, html_content = await _render_graduation_completion_email(
                session,
                first_name=profile.first_name,
                university_name=university_name,
            )

            queued = await queue_email_on_session(
                session,
                to_email=recipient_email,
                subject=subject,
                html_body=html_content,
                purpose=GRADUATION_COMPLETION_PURPOSE,
            )
            if not queued:
                await session.rollback()
                return False

            now = utc_now()
            result = await session.execute(
                update(Profile)
                .where(Profile.id == profile.id)
                .where(Profile.graduation_date.is_not(None))
                .where(Profile.graduation_date < today)
                .where(Profile.graduation_completion_email_sent_at.is_(None))
                .values(
                    graduation_completion_email_sent_at=now,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                await session.rollback()
                return False

            await session.commit()
            logger.info("Queued graduation completion email user=%s", user_id)
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
            await session.rollback()
            return (
                await session.execute(select(Profile).where(Profile.user_id == user_id))
            ).scalars().first()

    @staticmethod
    def _is_eligible(profile: Profile, *, today: date) -> bool:
        if profile.graduation_date is None:
            return False
        if profile.graduation_completion_email_sent_at is not None:
            return False
        return is_graduation_completed(profile.graduation_date, reference_date=today)


async def process_graduation_completion_emails(
    limit: int | None = None,
    *,
    session_factory=None,
) -> int:
    """Entrypoint for the existing scheduling loop (producer only)."""
    return await GraduationEmailService(
        session_factory=session_factory,
    ).process_graduation_completion_emails(limit=limit)


async def run_graduation_email_tick(
    *,
    session_factory=None,
    lease_owner: str | None = None,
    limit: int | None = None,
) -> dict[str, int]:
    """Queue eligible graduation emails, then deliver only that purpose.

    Used by the dedicated Celery worker and admin ``/admin/graduation/runcron``.
    Does not run connection reminders or other transactional producers.
    """
    from core.email_service import process_pending_emails

    batch_limit = DEFAULT_BATCH_LIMIT if limit is None else max(1, limit)
    logger.info("[graduation-email] Tick started limit=%s", batch_limit)
    queued = await process_graduation_completion_emails(
        limit=batch_limit,
        session_factory=session_factory,
    )
    delivered = await process_pending_emails(
        limit=batch_limit,
        session_factory=session_factory,
        lease_owner=lease_owner,
        purpose=GRADUATION_COMPLETION_PURPOSE,
    )
    logger.info(
        "[graduation-email] Tick finished queued=%s delivered=%s",
        queued,
        delivered,
    )
    return {"queued": queued, "delivered": delivered}
