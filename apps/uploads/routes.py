from __future__ import annotations

from fastapi import APIRouter, UploadFile, File, Form

from apps.profiles.schemas import ApiResponse
from common.exceptions import ApiError
from common.responses import success_response
from core.images import save_image, generate_download_url
import uuid

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_PREFIXES = {"profiles", "banners"}
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

router = APIRouter(tags=["2] User Management"])


@router.post("/uploads/image", response_model=ApiResponse)
async def upload_image(
    file: UploadFile = File(...),
    prefix: str = Form("profiles"),
):
    if prefix not in ALLOWED_PREFIXES:
        raise ApiError(f"Invalid prefix. Must be one of: {', '.join(ALLOWED_PREFIXES)}")

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise ApiError(f"Invalid file type. Must be one of: {', '.join(ALLOWED_CONTENT_TYPES)}")

    content = await file.read()

    if len(content) > MAX_IMAGE_SIZE:
        raise ApiError("File size exceeds 5MB limit")

    ext = file.filename.split(".")[-1] if file.filename and "." in file.filename else "png"
    file_name = f"{prefix}/{uuid.uuid4()}.{ext}"
    save_image(file_name=file_name, content=content, content_type=file.content_type or "image/png")

    return success_response(
        "image uploaded",
        {"key": file_name, "url": generate_download_url(file_name)},
        response_cls=ApiResponse,
    )
