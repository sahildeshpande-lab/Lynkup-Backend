"""Admin browser-session lifecycle (independent of user_id / refresh token)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import SecurityEventType, User
from apps.accounts.services.common_service import log_security_event
from apps.administration.db_models import (
    AdminSession,
    AdminSessionStatus,
    AdminSigningKey,
)
from common.exceptions import ApiError
from core.auth.config import settings as auth_settings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def admin_session_access_ttl() -> timedelta:
    minutes = max(1, int(auth_settings.admin_access_token_expire_minutes))
    return timedelta(minutes=minutes)


def compute_admin_session_expires_at(from_time: datetime | None = None) -> datetime:
    base = from_time or utc_now()
    if getattr(base, "tzinfo", None) is None:
        base = base.replace(tzinfo=timezone.utc)
    return base + admin_session_access_ttl()


def _as_utc(value: datetime) -> datetime:
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_admin_session_expired(session: AdminSession, *, now: datetime | None = None) -> bool:
    """True when expires_at is set and has passed (access window missed)."""
    expires_at = getattr(session, "expires_at", None)
    if expires_at is None:
        return False
    current = now or utc_now()
    return _as_utc(expires_at) <= _as_utc(current)


async def create_admin_session(
    db: AsyncSession,
    user: User,
    *,
    refresh_jti: str | None = None,
) -> AdminSession:
    """Create a new independent admin session UUID for a successful login."""
    now = utc_now()
    session = AdminSession(
        id=uuid4(),
        user_id=user.id,
        status=AdminSessionStatus.ACTIVE.value,
        refresh_jti=refresh_jti,
        expires_at=compute_admin_session_expires_at(now),
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    db.add(session)
    await db.flush()
    return session


async def get_active_admin_session(
    db: AsyncSession,
    session_id: UUID | str,
    *,
    user_id: UUID,
) -> AdminSession | None:
    """Return session only if active and within the access-aligned expiry window."""
    try:
        sid = session_id if isinstance(session_id, UUID) else UUID(str(session_id))
    except (TypeError, ValueError):
        return None

    stmt = select(AdminSession).where(AdminSession.id == sid)
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        return None
    if session.user_id != user_id:
        return None
    if session.status != AdminSessionStatus.ACTIVE.value:
        return None
    if is_admin_session_expired(session):
        return None
    return session


async def get_admin_session_for_refresh(
    db: AsyncSession,
    session_id: UUID | str,
    *,
    user_id: UUID,
) -> AdminSession | None:
    """Load an active session for refresh even if access-aligned expires_at has passed.

    Refresh JWT expiry remains the hard gate; a successful refresh slides expires_at.
    """
    try:
        sid = session_id if isinstance(session_id, UUID) else UUID(str(session_id))
    except (TypeError, ValueError):
        return None

    stmt = select(AdminSession).where(AdminSession.id == sid)
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        return None
    if session.user_id != user_id:
        return None
    if session.status != AdminSessionStatus.ACTIVE.value:
        return None
    return session


async def require_active_admin_session(
    db: AsyncSession,
    session_id: UUID | str | None,
    *,
    user_id: UUID,
) -> AdminSession:
    if not session_id:
        raise ApiError("Session expired. Please sign in again.")
    session = await get_active_admin_session(db, session_id, user_id=user_id)
    if session is None:
        raise ApiError("Session expired. Please sign in again.")
    return session


async def require_admin_session_for_refresh(
    db: AsyncSession,
    session_id: UUID | str | None,
    *,
    user_id: UUID,
) -> AdminSession:
    if not session_id:
        raise ApiError("Session expired. Please sign in again.")
    session = await get_admin_session_for_refresh(db, session_id, user_id=user_id)
    if session is None:
        raise ApiError("Session expired. Please sign in again.")
    return session


async def touch_admin_session(db: AsyncSession, session: AdminSession) -> None:
    """Update last_seen_at, throttled to avoid a write on every request."""
    now = utc_now()
    throttle = max(0, int(auth_settings.admin_signing_rate_limit_window_seconds))
    last_seen = getattr(session, "last_seen_at", None)
    if last_seen is not None:
        last = _as_utc(last_seen)
        if (now - last).total_seconds() < throttle:
            return
    session.last_seen_at = now
    if hasattr(session, "updated_at"):
        session.updated_at = now
    if hasattr(db, "add"):
        db.add(session)


async def extend_admin_session_after_refresh(
    db: AsyncSession,
    session: AdminSession,
    refresh_jti: str,
) -> None:
    """Rotate refresh jti and slide expires_at with the new access-token window."""
    now = utc_now()
    session.refresh_jti = refresh_jti
    session.expires_at = compute_admin_session_expires_at(now)
    session.updated_at = now
    session.last_seen_at = now
    db.add(session)


async def set_admin_session_refresh_jti(
    db: AsyncSession,
    session: AdminSession,
    refresh_jti: str,
) -> None:
    """Persist the current refresh JWT jti and slide session expiry (refresh path)."""
    await extend_admin_session_after_refresh(db, session, refresh_jti)


async def revoke_admin_session(
    db: AsyncSession,
    session: AdminSession,
    *,
    revoke_keys: bool = True,
    emit_event: bool = True,
) -> None:
    """Hard-delete an admin session and its signing keys.

    Keys are removed first to satisfy the ``admin_signing_keys.session_id`` FK.
    No revoked session row is retained.
    """
    session_id = session.id
    user_id = session.user_id
    now = utc_now()

    if emit_event:
        await log_security_event(
            db,
            user_id,
            SecurityEventType.SESSION_REVOKED,
            event_metadata={"session_id": str(session_id)},
        )

    # Always delete keys before the session row (FK). ``revoke_keys`` kept for API compat.
    _ = revoke_keys
    await db.execute(delete(AdminSigningKey).where(AdminSigningKey.session_id == session_id))
    await db.execute(delete(AdminSession).where(AdminSession.id == session_id))

    # Mark the in-memory object so callers (e.g. logout response) still see revoked state.
    session.status = AdminSessionStatus.REVOKED.value
    session.revoked_at = now
    session.updated_at = now
    session.refresh_jti = None
    session.expires_at = now


async def revoke_all_admin_sessions_for_user(db: AsyncSession, user_id: UUID) -> None:
    """Revoke every active admin session (and its keys) for a user."""
    rows = (
        await db.execute(
            select(AdminSession).where(
                AdminSession.user_id == user_id,
                AdminSession.status == AdminSessionStatus.ACTIVE.value,
            )
        )
    ).scalars().all()
    for session in rows:
        await revoke_admin_session(db, session, revoke_keys=True, emit_event=True)
