"""Orchestrates the full mobile security pipeline (outside frozen auth.py)."""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from core.security.mobile.app_attest import verify_ios_app_attest_assertion
from core.security.mobile.config import settings as mobile_settings
from core.security.mobile.device import MobileSecurityContext, bind_mobile_device
from core.security.mobile.play_integrity import verify_android_play_integrity
from core.security.mobile.rate_limit import RATE_LIMIT_MESSAGE, consume_mobile_rate_limit
from core.security.mobile.request_proof import verify_mobile_request_proof
from common.exceptions import ApiError
from apps.accounts.db_models import SecurityEventType
from core.security.mobile.audit import emit_mobile_security_event
from core.security.mobile.store import auth_failure


async def run_mobile_security_pipeline(
    request: Request,
    user: User,
    db: AsyncSession,
    *,
    include_proof: bool = True,
    proof_already_applied: bool = False,
) -> MobileSecurityContext:
    """Device bind → rate limit → platform attestation → request proof.

    When ``MOBILE_SECURITY_ENABLED`` is false, returns a lightweight context
    if ``X-Device-Id`` is present and the installation exists; otherwise builds
    a passthrough context without failing (backward compatible).
    """
    if not mobile_settings.mobile_security_enabled:
        # Compatibility: do not enforce. Optionally attach installation if present.
        from core.security.mobile.device import extract_device_id
        from apps.accounts.db_models import UserInstallation
        from sqlmodel import select

        device_id = extract_device_id(request)
        installation = None
        if device_id:
            installation = (
                await db.execute(
                    select(UserInstallation).where(
                        UserInstallation.user_id == user.id,
                        UserInstallation.device_id == device_id,
                    )
                )
            ).scalar_one_or_none()
        ctx = MobileSecurityContext(
            user=user,
            installation=installation,  # type: ignore[arg-type]
            device_id=device_id or "",
            platform=(getattr(installation, "platform", None) or None) if installation else None,
        )
        # When disabled and no installation, still allow — use a dummy only if needed.
        if installation is None:
            # Routes that only need User still work; context.installation may be None
            # when security is disabled. Callers must tolerate None installation when disabled.
            request.state.mobile_security = ctx
            return ctx
        request.state.mobile_security = ctx
        request.state.mobile_device_id = device_id
        return ctx

    # 1–5. Device binding + active installation
    ctx = await bind_mobile_device(db, request, user)

    # 6. Rate limit (before expensive vendor calls). Skip if proof path already consumed it.
    if not proof_already_applied:
        try:
            allowed = await consume_mobile_rate_limit(
                user_id=user.id,
                device_id=ctx.device_id,
            )
        except ApiError:
            await emit_mobile_security_event(
                db,
                user.id,
                SecurityEventType.MOBILE_SECURITY_REDIS_UNAVAILABLE,
                request=request,
                metadata={"reason": "rate_limit_redis", "device_id": ctx.device_id},
            )
            raise
        if not allowed:
            await emit_mobile_security_event(
                db,
                user.id,
                SecurityEventType.MOBILE_RATE_LIMIT_EXCEEDED,
                request=request,
                metadata={"device_id": ctx.device_id},
            )
            raise ApiError(RATE_LIMIT_MESSAGE)

    body = await request.body()

    # 7–8. Platform attestation
    platform = (ctx.platform or "").lower()
    if platform in {"android", "and"}:
        await verify_android_play_integrity(db, request, user, ctx, body=body)
    elif platform in {"ios", "iphone", "ipad"}:
        await verify_ios_app_attest_assertion(db, request, user, ctx, body=body)
    elif mobile_settings.android_integrity_enabled or mobile_settings.ios_attest_enabled:
        # Platform required when any attestation flag is on.
        await emit_mobile_security_event(
            db,
            user.id,
            SecurityEventType.MOBILE_REQUEST_PROOF_INVALID,
            request=request,
            metadata={"reason": "unknown_platform", "device_id": ctx.device_id},
        )
        raise auth_failure("unknown_platform")

    # 9–11. Timestamp / nonce / request proof (unless already applied by authenticate_request)
    if include_proof and not proof_already_applied:
        await verify_mobile_request_proof(
            request,
            user,
            installation=ctx.installation,
            skip_rate_limit=True,  # already applied above
            db=db,
        )

    return ctx
