from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import get_session
from core.security.auth import get_current_user
from apps.accounts.db_models import User
from apps.uploads.schemas import UploadResponse
from core.images.storage_service import storage_service

ALLOWED_PREFIXES = {"profiles", "banners"}

router = APIRouter(tags=["2] User Management"])


@router.post("/uploads/image", response_model=UploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    prefix: str = Form("profiles"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> UploadResponse:
    if prefix not in ALLOWED_PREFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid prefix. Must be one of: {', '.join(sorted(ALLOWED_PREFIXES))}",
        )

    user_identifier = getattr(current_user, "id", "user_1")

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

    return UploadResponse(
        status=result["status"],
        message=result["message"],
        data=result["data"],
    )
