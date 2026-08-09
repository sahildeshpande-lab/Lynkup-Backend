from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import User
from apps.feed.db_models.media_asset_db_model import MediaAsset
from apps.profiles.db_models.profile_db_model import Profile
from apps.user_deletion.config import settings as deletion_settings
from common.enums import UserStatus
from core.database.session import async_session_factory

logger = logging.getLogger(__name__)

# Stable advisory lock key for multi-instance cron coordination.
_ACCOUNT_DELETION_LOCK_KEY = 8_046_091_427


@dataclass
class ExternalCleanupTargets:
    user_id: UUID
    firebase_uid: str | None = None
    stream_user_id: str | None = None
    spaces_keys: list[str] = field(default_factory=list)


@dataclass
class PurgeBatchStats:
    eligible: int = 0
    purged: int = 0
    failed: int = 0
    skipped_lock: bool = False


class AccountDeletionService:
    """Orchestrate permanent purge after the grace period."""

    def __init__(self, *, batch_size: int | None = None) -> None:
        self._batch_size = (
            deletion_settings.account_deletion_batch_size
            if batch_size is None
            else batch_size
        )

    @classmethod
    async def run_purge_batch(cls) -> PurgeBatchStats:
        return await cls()._run_purge_batch()

    async def _run_purge_batch(self) -> PurgeBatchStats:
        stats = PurgeBatchStats()
        async with async_session_factory() as lock_session:
            acquired = await self._try_advisory_lock(lock_session)
            if not acquired:
                stats.skipped_lock = True
                logger.info(
                    "[account-deletion-cron] Skipped: another instance holds the lock"
                )
                return stats

            try:
                user_ids = await self.find_eligible_user_ids(lock_session)
                stats.eligible = len(user_ids)
                logger.info(
                    "[account-deletion-cron] Eligible users=%s batch_size=%s",
                    stats.eligible,
                    self._batch_size,
                )

                for user_id in user_ids:
                    try:
                        await self.purge_user(user_id)
                        stats.purged += 1
                    except Exception:
                        stats.failed += 1
                        logger.exception(
                            "[account-deletion-cron] Purge failed user_id=%s",
                            user_id,
                        )
            finally:
                await self._release_advisory_lock(lock_session)

        logger.info(
            "[account-deletion-cron] Batch complete eligible=%s purged=%s failed=%s",
            stats.eligible,
            stats.purged,
            stats.failed,
        )
        return stats

    async def find_eligible_user_ids(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> list[UUID]:
        timestamp = now or datetime.now(timezone.utc)
        batch_limit = self._batch_size if limit is None else limit
        stmt = (
            select(User.id)
            .where(
                User.status == UserStatus.deleting,
                User.purge_after.is_not(None),
                User.purge_after <= timestamp,
            )
            .order_by(User.purge_after.asc())
            .limit(batch_limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    async def collect_external_targets(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> ExternalCleanupTargets | None:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            return None

        targets = ExternalCleanupTargets(
            user_id=user_id,
            firebase_uid=user.firebase_uid,
            stream_user_id=str(user.id),
        )

        profile = (
            await session.execute(select(Profile).where(Profile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is not None:
            for key in (profile.profile_photo_url, profile.banner_photo_url):
                if key and str(key).strip():
                    targets.spaces_keys.append(str(key).strip())

        media_keys = (
            await session.execute(
                select(MediaAsset.key).where(MediaAsset.owner_user_id == user_id)
            )
        ).scalars().all()
        for key in media_keys:
            if key and str(key).strip():
                targets.spaces_keys.append(str(key).strip())

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_keys: list[str] = []
        for key in targets.spaces_keys:
            if key not in seen:
                seen.add(key)
                unique_keys.append(key)
        targets.spaces_keys = unique_keys
        return targets

    async def purge_user(self, user_id: UUID) -> None:
        """Collect external IDs → CALL purge_user_data → external cleanup."""
        async with async_session_factory() as session:
            targets = await self.collect_external_targets(session, user_id)
            if targets is None:
                logger.info(
                    "[account-deletion] User already gone user_id=%s",
                    user_id,
                )
                return

            # Re-check eligibility inside this session before hard delete.
            user = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one()
            now = datetime.now(timezone.utc)
            if user.status != UserStatus.deleting or user.purge_after is None:
                logger.info(
                    "[account-deletion] Skip non-eligible user_id=%s status=%s",
                    user_id,
                    user.status,
                )
                return
            purge_after = user.purge_after
            if purge_after.tzinfo is None:
                purge_after = purge_after.replace(tzinfo=timezone.utc)
            if purge_after > now:
                logger.info(
                    "[account-deletion] Skip; still in grace period user_id=%s",
                    user_id,
                )
                return

            try:
                await session.execute(
                    text("CALL purge_user_data(:user_id)"),
                    {"user_id": str(user_id)},
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception(
                    "[account-deletion] DB purge rolled back user_id=%s",
                    user_id,
                )
                raise

        await self._cleanup_external(targets)

    async def _cleanup_external(self, targets: ExternalCleanupTargets) -> None:
        await self._cleanup_firebase(targets)
        await self._cleanup_stream(targets)
        await self._cleanup_spaces(targets)

    async def _cleanup_firebase(self, targets: ExternalCleanupTargets) -> None:
        from core.auth.services import delete_firebase_user

        uid = targets.firebase_uid
        if not uid or str(uid).startswith("admin-"):
            return
        try:
            delete_firebase_user(uid)
            logger.info(
                "[account-deletion] Firebase user deleted uid=%s user_id=%s",
                uid,
                targets.user_id,
            )
        except Exception:
            logger.exception(
                "[account-deletion] Firebase delete failed (retryable) uid=%s user_id=%s",
                uid,
                targets.user_id,
            )

    async def _cleanup_stream(self, targets: ExternalCleanupTargets) -> None:
        from apps.chat.service import delete_stream_user_best_effort

        if not targets.stream_user_id:
            return
        try:
            await delete_stream_user_best_effort(targets.stream_user_id)
        except Exception:
            logger.exception(
                "[account-deletion] Stream delete failed (retryable) user_id=%s",
                targets.user_id,
            )

    async def _cleanup_spaces(self, targets: ExternalCleanupTargets) -> None:
        from core.images.storage_service import delete_file

        for key in targets.spaces_keys:
            try:
                delete_file(key)
            except Exception:
                logger.exception(
                    "[account-deletion] Spaces delete failed (retryable) key=%s user_id=%s",
                    key,
                    targets.user_id,
                )

    def _is_postgres(self, session: AsyncSession) -> bool:
        bind = session.get_bind()
        return bool(bind is not None and bind.dialect.name == "postgresql")

    async def _try_advisory_lock(self, session: AsyncSession) -> bool:
        if not self._is_postgres(session):
            return True
        result = await session.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": _ACCOUNT_DELETION_LOCK_KEY},
        )
        locked = result.scalar()
        return bool(locked)

    async def _release_advisory_lock(self, session: AsyncSession) -> None:
        if not self._is_postgres(session):
            return
        try:
            await session.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": _ACCOUNT_DELETION_LOCK_KEY},
            )
        except Exception:
            logger.exception("[account-deletion-cron] Failed releasing advisory lock")
