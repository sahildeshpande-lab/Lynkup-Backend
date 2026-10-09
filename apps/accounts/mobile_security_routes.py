"""Mobile security enrollment endpoints (App Attest / Android Integrity enroll)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from common.exceptions import ApiError
from common.schemas import ApiResponse
from core.database.session import get_session
from core.security.auth import get_current_user
from core.security.mobile.app_attest import create_app_attest_challenge, register_app_attest_key
from core.security.mobile.config import settings as mobile_settings
from core.security.mobile.device import bind_mobile_device
from core.security.mobile.hmac_keys import ensure_installation_hmac_secret
from core.security.mobile.play_integrity import verify_android_play_integrity

router = APIRouter(prefix="/auth/mobile-security", tags=["Mobile Security"])


class AppAttestRegisterRequest(BaseModel):
    keyId: str = Field(min_length=1)
    attestationObject: str = Field(min_length=1)


@router.post("/app-attest/challenge", response_model=ApiResponse)
async def app_attest_challenge(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    if current_user.role != "user":
        raise ApiError("Insufficient permissions")
    return await create_app_attest_challenge(db, request, current_user)


@router.post("/app-attest/register", response_model=ApiResponse)
async def app_attest_register(
    payload: AppAttestRegisterRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    if current_user.role != "user":
        raise ApiError("Insufficient permissions")
    return await register_app_attest_key(
        db,
        request,
        current_user,
        key_id=payload.keyId,
        attestation_object=payload.attestationObject,
    )


@router.post("/android/enroll", response_model=ApiResponse)
async def android_integrity_enroll(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Validate FE integrity_token (header or JSON body) and persist Android security state.

    Requires ``X-Device-Id``, ``X-Timestamp``, ``X-Nonce``, and a Play Integrity
    token whose ``requestHash`` is SHA-256 of the mobile canonical request
    (same fields as HMAC). Token header: ``X-Play-Integrity-Token`` (or
    ``X-Play-Integrity`` / JSON body fields).
    """
    if current_user.role != "user":
        raise ApiError("Insufficient permissions")
    if not mobile_settings.android_integrity_enabled:
        raise ApiError("Android Play Integrity is not enabled")

    # Temporarily enable binding path even if master mobile flag is off (enrollment).
    ctx = await bind_mobile_device(db, request, current_user)
    body = await request.body()
    # Token from X-Play-Integrity-Token and/or JSON {"integrityToken"|"integrity_token": "..."}.
    await verify_android_play_integrity(db, request, current_user, ctx, body=body)
    assert ctx.installation is not None
    secret = ensure_installation_hmac_secret(ctx.installation)
    db.add(ctx.installation)
    await db.commit()

    return ApiResponse(
        status=True,
        message="Android installation enrolled",
        data={
            "deviceId": ctx.device_id,
            "integrityLevel": ctx.installation.android_integrity_level,
            "hmacSecret": secret,
        },
    )
