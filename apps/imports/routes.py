from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.imports.enums import ImportType
from apps.imports.services import import_upload
from common.schemas import ApiResponse
from core.database.session import get_session
from core.security.auth import get_current_admin

router = APIRouter(tags=["4] Admin Management"])


@router.post("/admin/import", response_model=ApiResponse)
async def admin_import(
    type: ImportType = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    data = await import_upload(
        import_type=type,
        file=file,
        db=db,
        actor_user_id=current_user.id,
        actor_role=getattr(current_user.role, "value", current_user.role),
    )
    message = (
        "Import completed with some failures"
        if data["failedCount"] > 0
        else "Import completed"
    )
    return ApiResponse(status=True, message=message, data=data)
