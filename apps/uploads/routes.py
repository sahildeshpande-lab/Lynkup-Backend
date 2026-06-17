from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import get_session
from core.security.auth import get_current_user
from apps.accounts.db_models import User
from apps.profiles.schemas import ApiResponse
from core.images import save_image, generate_download_url
import uuid

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_PREFIXES = {"profiles", "banners"}
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

router = APIRouter(tags=["2] User Management"])


@router.post("/uploads/image")
async def upload_image(
    file: UploadFile = File(...),
    prefix: str = Form("profiles"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    if prefix not in ALLOWED_PREFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid prefix. Must be one of: {', '.join(ALLOWED_PREFIXES)}",
        )

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Must be one of: {', '.join(ALLOWED_CONTENT_TYPES)}",
        )

    content = await file.read()

    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 5MB limit",
        )

    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "png"
    file_name = f"{prefix}/{uuid.uuid4()}.{ext}"
    save_image(file_name=file_name, content=content, content_type=file.content_type or "image/png")

    return ApiResponse(
        message="image uploaded",
        data={
            "key": file_name,
            "url": generate_download_url(file_name),
        },
    )
