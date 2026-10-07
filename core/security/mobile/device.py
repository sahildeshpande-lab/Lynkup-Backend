"""Device binding against ``user_installations`` (not OTP ``is_device_verified``)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import SecurityEventType, User, UserInstallation
from common.exceptions import ApiError
from core.security.mobile.audit import emit_mobile_security_event
from core.security.mobile.request_proof import HEADER_DEVICE_ID
from core.security.mobile.store import GENERIC_AUTH_FAILURE


@dataclass(frozen=True, slots=True)
class MobileSecurityContext:
    user: User
    installation: UserInstallation | None
    device_id: str
    platform: str | None


def extract_device_id(request: Request) -> str:
    return (request.headers.get(HEADER_DEVICE_ID) or "").strip()


async def load_active_installation(
    db: AsyncSession,
    user: User,
    device_id: str,
    *,
    request: Request | None = None,
) -> UserInstallation:
    """Load and validate ``(user_id, device_id)`` installation binding."""
    if not device_id:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.MOBILE_MISSING_DEVICE,
            request=request,
            metadata={"reason": "missing_device_id"},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    stmt = select(UserInstallation).where(
        UserInstallation.user_id == user.id,
        UserInstallation.device_id == device_id,
    )
    installation = (await db.execute(stmt)).scalar_one_or_none()
    if installation is None:
        # Distinguish wrong-owner vs unknown without leaking which.
        other = (
            await db.execute(
                select(UserInstallation.id).where(UserInstallation.device_id == device_id).limit(1)
            )
        ).scalar_one_or_none()
        event = (
            SecurityEventType.MOBILE_UNKNOWN_DEVICE
            if other is None
            else SecurityEventType.UNAUTHORIZED_ACCESS
        )
        await emit_mobile_security_event(
            db,
            user.id,
            event,
            request=request,
            metadata={
                "reason": "unknown_or_foreign_device",
                "device_id": device_id,
            },
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    if installation.user_id != user.id or installation.device_id != device_id:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.UNAUTHORIZED_ACCESS,
            request=request,
            metadata={"reason": "user_device_mismatch", "device_id": device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    if not installation.is_active:
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.MOBILE_INACTIVE_DEVICE,
            request=request,
            metadata={"reason": "inactive_installation", "device_id": device_id},
        )
        raise ApiError(GENERIC_AUTH_FAILURE)

    await emit_mobile_security_event(
        db,
        user.id,
        SecurityEventType.MOBILE_DEVICE_VERIFIED,
        request=request,
        metadata={
            "device_id": device_id,
            "platform": installation.platform,
            "installation_id": str(installation.id),
        },
    )
    return installation


async def bind_mobile_device(
    db: AsyncSession,
    request: Request,
    user: User,
) -> MobileSecurityContext:
    device_id = extract_device_id(request)
    installation = await load_active_installation(db, user, device_id, request=request)
    ctx = MobileSecurityContext(
        user=user,
        installation=installation,
        device_id=device_id,
        platform=(installation.platform or "").strip().lower() or None,
    )
    request.state.mobile_security = ctx
    request.state.mobile_device_id = device_id
    return ctx
