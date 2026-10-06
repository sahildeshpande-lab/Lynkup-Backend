"""Bounded recovery of durable Celery work.

Reconciliation is a safety net: it atomically locates recoverable rows in
existing business tables, then republishes the already-migrated Celery tasks.
It does not execute export, email, moderation, deletion, or spotlight logic.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from core.celery_worker.config import CeleryTaskQueue
from core.jobs.config import settings as reconciliation_settings

logger = logging.getLogger(__name__)

EXPORT_PROCESS_TASK = "kampulynk.export.process"
NOTIFICATION_CAMPAIGN_DISPATCH_TASK = "kampulynk.notification.campaign.dispatch"
TRANSACTIONAL_EMAIL_TICK = "kampulynk.email.transactional.tick"
BULK_EMAIL_TICK = "kampulynk.email.bulk.tick"
MODERATION_TICK = "kampulynk.moderation.tick"
DELETION_TICK = "kampulynk.deletion.tick"
SPOTLIGHT_TICK = "kampulynk.spotlight.tick"

BACKGROUND_QUEUE = CeleryTaskQueue.BACKGROUND_QUEUE.value
NOTIFICATIONS_QUEUE = CeleryTaskQueue.NOTIFICATIONS_QUEUE.value
TRANSACTIONAL_QUEUE = CeleryTaskQueue.TRANSACTIONAL_QUEUE.value
BULK_EMAIL_QUEUE = CeleryTaskQueue.BULK_EMAIL_QUEUE.value
SPOTLIGHT_QUEUE = CeleryTaskQueue.SPOTLIGHTS_QUEUE.value
EXPORTS_QUEUE = CeleryTaskQueue.EXPORTS_QUEUE.value

# Pending work older than two email ticks is treated as unpublished/stuck.
_STALE_PENDING_SECONDS = 120
# Bulk deliveries left in processing after a worker crash.
_STALE_BULK_PROCESSING_SECONDS = 300

Publisher = Callable[..., None]


@dataclass
class ReconciliationOutcome:
    scanned: int = 0
    claimed: int = 0
    republished: int = 0
    skipped: int = 0
    failed: int = 0
    families: dict[str, int] = field(default_factory=dict)

    def as_log_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["families"] = dict(self.families)
        return payload


@dataclass(frozen=True)
class _PublishAction:
    task_name: str
    queue: str
    args: tuple[str, ...] = ()


def default_publisher(name: str, *, queue: str, args: Sequence[str] | None = None) -> None:
    from core.celery_worker.celery_app import celery_app

    celery_app.send_task(name, args=list(args or ()), queue=queue)


def recoverable_notification_campaigns_stmt(limit: int):
    from apps.notifications.db_models import NotificationCampaign
    from common.enums import NotificationCampaignStatus

    stale_before = datetime.now(timezone.utc) - timedelta(seconds=_STALE_PENDING_SECONDS)
    return (
        select(NotificationCampaign.id)
        .where(NotificationCampaign.status == NotificationCampaignStatus.draft)
        .where(NotificationCampaign.is_active.is_(True))
        .where(NotificationCampaign.created_at < stale_before)
        .order_by(NotificationCampaign.created_at.asc(), NotificationCampaign.id.asc())
        .limit(limit)
    )


def recoverable_exports_stmt(limit: int):
    from apps.export.enums import DataExportStatus
    from apps.export.models import DataExportRequest

    db_now = func.current_timestamp()
    return (
        select(DataExportRequest.id)
        .where(
            or_(
                DataExportRequest.status == DataExportStatus.queued,
                and_(
                    DataExportRequest.status == DataExportStatus.processing,
                    DataExportRequest.lease_expires_at < db_now,
                ),
            )
        )
        .order_by(DataExportRequest.created_at.asc(), DataExportRequest.id.asc())
        .limit(limit)
    )


def recoverable_transactional_emails_stmt(limit: int):
    from apps.accounts.db_models import TransactionalEmailLog

    db_now = func.current_timestamp()
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=_STALE_PENDING_SECONDS)
    expired_lease = or_(
        TransactionalEmailLog.lease_expires_at.is_(None),
        TransactionalEmailLog.lease_expires_at < db_now,
    )
    return (
        select(TransactionalEmailLog.id)
        .where(TransactionalEmailLog.is_send.is_(False))
        .where(expired_lease)
        .where(
            or_(
                TransactionalEmailLog.lease_owner.is_not(None),
                TransactionalEmailLog.created_at < stale_before,
            )
        )
        .order_by(TransactionalEmailLog.created_at.asc(), TransactionalEmailLog.id.asc())
        .limit(limit)
    )


def recoverable_bulk_deliveries_stmt(limit: int):
    from apps.bulk_send.enums import EmailCampaignStatus, EmailDeliveryStatus
    from apps.bulk_send.models import EmailCampaign, EmailDelivery

    stale_processing = datetime.now(timezone.utc) - timedelta(
        seconds=_STALE_BULK_PROCESSING_SECONDS
    )
    active_campaigns = (EmailCampaignStatus.queued, EmailCampaignStatus.processing)
    return (
        select(EmailDelivery)
        .join(EmailCampaign, EmailCampaign.id == EmailDelivery.campaign_id)
        .where(EmailDelivery.status == EmailDeliveryStatus.processing)
        .where(EmailCampaign.status.in_(active_campaigns))
        .where(
            or_(
                EmailDelivery.last_attempt_at.is_(None),
                EmailDelivery.last_attempt_at < stale_processing,
            )
        )
        .order_by(EmailDelivery.created_at.asc(), EmailDelivery.id.asc())
        .limit(limit)
    )


def recoverable_moderation_posts_stmt(limit: int):
    from apps.feed.db_models import Post
    from core.jobs.claims import (
        _moderation_lease_expired,
        _needs_auto_moderation_scan,
        _SKIP_POST_STATES,
    )

    db_now = func.current_timestamp()
    return (
        select(Post.id)
        .where(Post.state.notin_(_SKIP_POST_STATES))
        .where(_needs_auto_moderation_scan(Post.auto_moderation_scanned_at, Post.updated_at))
        .where(
            _moderation_lease_expired(
                Post.moderation_lease_owner,
                Post.moderation_lease_expires_at,
                db_now,
            )
        )
        .order_by(Post.updated_at.asc(), Post.id.asc())
        .limit(limit)
    )


def recoverable_moderation_comments_stmt(limit: int):
    from apps.engagement.db_models import Comment
    from core.jobs.claims import _moderation_lease_expired, _needs_auto_moderation_scan

    db_now = func.current_timestamp()
    return (
        select(Comment.id)
        .where(Comment.is_deleted.is_(False))
        .where(
            _needs_auto_moderation_scan(
                Comment.auto_moderation_scanned_at,
                Comment.updated_at,
            )
        )
        .where(
            _moderation_lease_expired(
                Comment.moderation_lease_owner,
                Comment.moderation_lease_expires_at,
                db_now,
            )
        )
        .order_by(Comment.updated_at.asc(), Comment.id.asc())
        .limit(limit)
    )


def recoverable_deletion_users_stmt(limit: int):
    from apps.accounts.db_models import User
    from common.enums import UserStatus

    db_now = func.current_timestamp()
    return (
        select(User.id)
        .where(User.status == UserStatus.deleting)
        .where(User.purge_after.is_not(None))
        .where(User.purge_after <= db_now)
        .order_by(User.purge_after.asc(), User.id.asc())
        .limit(limit)
    )


async def _lock_all(session: AsyncSession, stmt):
    """Lock eligible rows with SKIP LOCKED; fall back when the dialect cannot."""
    try:
        locked = stmt.with_for_update(skip_locked=True)
        return await session.execute(locked)
    except Exception:
        await session.rollback()
        return await session.execute(stmt)


async def _lock_ids(session: AsyncSession, stmt) -> list[UUID]:
    result = await _lock_all(session, stmt)
    return list(result.scalars().all())


def _publish(
    publisher: Publisher,
    pending: list[_PublishAction],
    outcome: ReconciliationOutcome,
) -> None:
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for action in pending:
        key = (action.task_name, action.args)
        if key in seen:
            outcome.skipped += 1
            continue
        seen.add(key)
        try:
            publisher(action.task_name, queue=action.queue, args=list(action.args))
        except Exception:
            outcome.failed += 1
            logger.exception(
                "Reconciliation publish failed task=%s queue=%s",
                action.task_name,
                action.queue,
            )
            continue
        outcome.republished += 1
        outcome.families[action.task_name] = outcome.families.get(action.task_name, 0) + 1


async def _recover_notification_campaigns(
    session: AsyncSession,
    *,
    limit: int,
    outcome: ReconciliationOutcome,
    pending: list[_PublishAction],
) -> None:
    if limit <= 0:
        return
    ids = await _lock_ids(session, recoverable_notification_campaigns_stmt(limit))
    outcome.scanned += len(ids)
    await session.commit()
    if not ids:
        return
    outcome.claimed += len(ids)
    for campaign_id in ids:
        pending.append(
            _PublishAction(
                task_name=NOTIFICATION_CAMPAIGN_DISPATCH_TASK,
                queue=NOTIFICATIONS_QUEUE,
                args=(str(campaign_id),),
            )
        )


async def _recover_exports(
    session: AsyncSession,
    *,
    limit: int,
    outcome: ReconciliationOutcome,
    pending: list[_PublishAction],
) -> None:
    if limit <= 0:
        return
    ids = await _lock_ids(session, recoverable_exports_stmt(limit))
    outcome.scanned += len(ids)
    await session.commit()
    if not ids:
        return
    outcome.claimed += len(ids)
    for export_id in ids:
        pending.append(
            _PublishAction(
                task_name=EXPORT_PROCESS_TASK,
                queue=EXPORTS_QUEUE,
                args=(str(export_id),),
            )
        )


async def _recover_transactional_email(
    session: AsyncSession,
    *,
    limit: int,
    outcome: ReconciliationOutcome,
    pending: list[_PublishAction],
) -> None:
    if limit <= 0:
        return
    ids = await _lock_ids(session, recoverable_transactional_emails_stmt(limit))
    outcome.scanned += len(ids)
    await session.commit()
    if not ids:
        return
    outcome.claimed += len(ids)
    pending.append(
        _PublishAction(task_name=TRANSACTIONAL_EMAIL_TICK, queue=TRANSACTIONAL_QUEUE)
    )


async def _recover_bulk_email(
    session: AsyncSession,
    *,
    limit: int,
    outcome: ReconciliationOutcome,
    pending: list[_PublishAction],
) -> None:
    if limit <= 0:
        return
    from apps.bulk_send.enums import EmailDeliveryStatus
    from apps.bulk_send.models import EmailDelivery

    stmt = recoverable_bulk_deliveries_stmt(limit)
    try:
        result = await session.execute(
            stmt.with_for_update(skip_locked=True, of=EmailDelivery)
        )
    except Exception:
        await session.rollback()
        result = await _lock_all(session, stmt)
    deliveries = list(result.scalars().all())
    outcome.scanned += len(deliveries)
    if not deliveries:
        await session.commit()
        return

    db_now = func.current_timestamp()
    reset_ids: list[UUID] = []
    sent_ids: list[UUID] = []
    for delivery in deliveries:
        if delivery.sendgrid_message_id:
            sent_ids.append(delivery.id)
        else:
            reset_ids.append(delivery.id)

    if sent_ids:
        await session.execute(
            update(EmailDelivery)
            .where(EmailDelivery.id.in_(sent_ids))
            .where(EmailDelivery.status == EmailDeliveryStatus.processing)
            .values(
                status=EmailDeliveryStatus.sent,
                delivered_at=db_now,
                updated_at=db_now,
            )
            .execution_options(synchronize_session=False)
        )
        outcome.skipped += len(sent_ids)

    if reset_ids:
        await session.execute(
            update(EmailDelivery)
            .where(EmailDelivery.id.in_(reset_ids))
            .where(EmailDelivery.status == EmailDeliveryStatus.processing)
            .values(
                status=EmailDeliveryStatus.pending,
                updated_at=db_now,
            )
            .execution_options(synchronize_session=False)
        )
        outcome.claimed += len(reset_ids)

    await session.commit()
    if reset_ids:
        pending.append(_PublishAction(task_name=BULK_EMAIL_TICK, queue=BULK_EMAIL_QUEUE))


async def _recover_moderation(
    session: AsyncSession,
    *,
    limit: int,
    outcome: ReconciliationOutcome,
    pending: list[_PublishAction],
) -> None:
    if limit <= 0:
        return
    from apps.moderation.config import settings as auto_moderation_settings

    if not auto_moderation_settings.enabled:
        return

    post_ids = await _lock_ids(session, recoverable_moderation_posts_stmt(limit))
    remaining = max(0, limit - len(post_ids))
    comment_ids = await _lock_ids(
        session, recoverable_moderation_comments_stmt(remaining)
    )
    found = len(post_ids) + len(comment_ids)
    outcome.scanned += found
    await session.commit()
    if not found:
        return
    outcome.claimed += found
    pending.append(_PublishAction(task_name=MODERATION_TICK, queue=BACKGROUND_QUEUE))


async def _recover_deletion(
    session: AsyncSession,
    *,
    limit: int,
    outcome: ReconciliationOutcome,
    pending: list[_PublishAction],
) -> None:
    if limit <= 0:
        return
    ids = await _lock_ids(session, recoverable_deletion_users_stmt(min(limit, 1)))
    outcome.scanned += len(ids)
    await session.commit()
    if not ids:
        return
    outcome.claimed += len(ids)
    pending.append(_PublishAction(task_name=DELETION_TICK, queue=BACKGROUND_QUEUE))


async def _spotlight_lock_is_free(engine: AsyncEngine | None) -> bool:
    if engine is None:
        return True
    dialect = getattr(getattr(engine, "dialect", None), "name", "")
    if dialect != "postgresql":
        return True

    from apps.learningspotlight.cron import _LEARNING_SPOTLIGHT_LOCK_KEY

    async with engine.connect() as connection:
        acquired = bool(
            (
                await connection.execute(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": _LEARNING_SPOTLIGHT_LOCK_KEY},
                )
            ).scalar()
        )
        if acquired:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": _LEARNING_SPOTLIGHT_LOCK_KEY},
            )
            await connection.commit()
        return acquired


async def _recover_spotlight(
    session: AsyncSession,
    *,
    engine: AsyncEngine | None,
    outcome: ReconciliationOutcome,
    pending: list[_PublishAction],
) -> None:
    from apps.learningspotlight.cron import resolve_spotlight_business_date
    from apps.learningspotlight.services.daily_run_service import fetch_completed_daily_run
    from apps.profiles.db_models.learning_recommendation_settings_db_model import (
        LearningRecommendationSettings,
    )
    from apps.recommendations.services.recommendation_settings_service import (
        RecommendationSettingsService,
    )

    settings_row = await RecommendationSettingsService().get_persisted_settings(session)
    if settings_row is not None and not settings_row.is_enabled:
        await session.commit()
        return

    business_date = resolve_spotlight_business_date()
    completed = await fetch_completed_daily_run(session, business_date)
    is_running = bool(getattr(settings_row, "is_running", False))
    lock_free = await _spotlight_lock_is_free(engine)
    outcome.scanned += 1

    if completed is not None:
        if is_running and lock_free:
            await session.execute(
                update(LearningRecommendationSettings)
                .where(LearningRecommendationSettings.is_running.is_(True))
                .values(is_running=False, updated_at=func.current_timestamp())
                .execution_options(synchronize_session=False)
            )
            outcome.skipped += 1
        else:
            outcome.skipped += 1
        await session.commit()
        return

    if is_running and not lock_free:
        outcome.skipped += 1
        await session.commit()
        return

    if is_running and lock_free:
        await session.execute(
            update(LearningRecommendationSettings)
            .where(LearningRecommendationSettings.is_running.is_(True))
            .values(is_running=False, updated_at=func.current_timestamp())
            .execution_options(synchronize_session=False)
        )

    await session.commit()
    outcome.claimed += 1
    pending.append(_PublishAction(task_name=SPOTLIGHT_TICK, queue=SPOTLIGHT_QUEUE))


async def run_reconciliation_batch(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine | None = None,
    publisher: Publisher | None = None,
    batch_size: int | None = None,
    enabled: bool | None = None,
) -> ReconciliationOutcome:
    """Scan a bounded set of recoverable durable rows and republish existing tasks."""
    if enabled is None:
        enabled = reconciliation_settings.enabled
    if not enabled:
        logger.info("Reconciliation disabled; skipping durable-work scan")
        return ReconciliationOutcome()

    limit = int(batch_size if batch_size is not None else reconciliation_settings.batch_size)
    if limit < 1:
        limit = 1
    publish = publisher or default_publisher
    outcome = ReconciliationOutcome()
    pending: list[_PublishAction] = []
    remaining = limit

    async with session_factory() as session:
        await _recover_exports(
            session, limit=remaining, outcome=outcome, pending=pending
        )
        remaining = max(0, limit - outcome.claimed)

        await _recover_transactional_email(
            session, limit=remaining, outcome=outcome, pending=pending
        )
        remaining = max(0, limit - outcome.claimed)

        await _recover_bulk_email(
            session, limit=remaining, outcome=outcome, pending=pending
        )
        remaining = max(0, limit - outcome.claimed)

        await _recover_notification_campaigns(
            session, limit=remaining, outcome=outcome, pending=pending
        )
        remaining = max(0, limit - outcome.claimed)

        await _recover_moderation(
            session, limit=remaining, outcome=outcome, pending=pending
        )
        remaining = max(0, limit - outcome.claimed)

        await _recover_deletion(
            session, limit=remaining, outcome=outcome, pending=pending
        )
        remaining = max(0, limit - outcome.claimed)

        if remaining > 0:
            await _recover_spotlight(
                session, engine=engine, outcome=outcome, pending=pending
            )

    _publish(publish, pending, outcome)
    logger.info("Reconciliation batch complete %s", outcome.as_log_dict())
    return outcome
