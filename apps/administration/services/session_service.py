"""Admin browser-session lifecycle (independent of user_id / refresh token)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import SecurityEventType, User
from apps.accounts.services.common_service import log_security_event
from apps.administration.db_models import (
    AdminSession,
    AdminSessionStatus,
    AdminSigningKey,
    AdminSigningKeyStatus,
)
from common.exceptions import ApiError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def create_admin_session(db: AsyncSession, user: User) -> AdminSession:
    """Create a new independent admin session UUID for a successful login.

    Session remains active until logout or password change/reset revokes it.
    """
    now = utc_now()
    session = AdminSession(
        id=uuid4(),
        user_id=user.id,
        status=AdminSessionStatus.ACTIVE.value,
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


async def touch_admin_session(db: AsyncSession, session: AdminSession) -> None:
    session.last_seen_at = utc_now()
    session.updated_at = session.last_seen_at
    db.add(session)


async def revoke_admin_session(
    db: AsyncSession,
    session: AdminSession,
    *,
    revoke_keys: bool = True,
    emit_event: bool = True,
) -> None:
    now = utc_now()
    if session.status != AdminSessionStatus.REVOKED.value:
        session.status = AdminSessionStatus.REVOKED.value
        session.revoked_at = now
        session.updated_at = now
        db.add(session)

    if revoke_keys:
        await db.execute(
            update(AdminSigningKey)
            .where(
                AdminSigningKey.session_id == session.id,
                AdminSigningKey.status == AdminSigningKeyStatus.ACTIVE.value,
            )
            .values(
                status=AdminSigningKeyStatus.REVOKED.value,
                revoked_at=now,
                updated_at=now,
            )
        )

    if emit_event:
        await log_security_event(
            db,
            session.user_id,
            SecurityEventType.SESSION_REVOKED,
            event_metadata={"session_id": str(session.id)},
        )


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
