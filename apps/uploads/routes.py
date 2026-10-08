from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from apps.accounts.db_models import User
from apps.uploads.schemas import UploadResponse
from core.images.storage_service import storage_service
from core.security.auth import get_current_user_moderator_or_superadmin

ALLOWED_PREFIXES = {"profiles", "banners"}

router = APIRouter(tags=["2] User Management"])


@router.post("/uploads/image", response_model=UploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    prefix: str = Form("profiles"),
    current_user: User = Depends(get_current_user_moderator_or_superadmin),
) -> UploadResponse:
    """Upload a profile/banner image.

    Auth via :func:`get_current_user_moderator_or_superadmin`:
    - Web admin / staff: JWT + Origin + RSA request signature
    - Mobile app user: Firebase / user JWT
    """
    if prefix not in ALLOWED_PREFIXES:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": f"Invalid prefix. Must be one of: {', '.join(sorted(ALLOWED_PREFIXES))}"},
        )

    user_identifier = current_user.id

    try:
        if prefix == "banners":
            result = await storage_service.upload_banner(
                file=file,
                banner_id=user_identifier,
            )
        else:
            result = await storage_service.upload_profile(
                file=file,
                user_id=user_identifier,
            )
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    return UploadResponse(
        status=result["status"],
        message=result["message"],
        data=result["data"],
    )
