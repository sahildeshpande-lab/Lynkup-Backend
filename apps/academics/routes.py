from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.academics import services
from apps.academics.schemas import (
    AcademicCatalogPatchRequest,
    AcademicCatalogSoftDeleteRequest,
    AcademicInterestBulkRequest,
    CatalogBulkApiResponse,
    CatalogBulkOperationType,
    CatalogBulkRequest,
    CountryBulkRequest,
    UniversityBulkRequest,
)
from apps.accounts.db_models import User
from common.schemas import ApiResponse
from core.database.session import get_session
from core.security.auth import get_current_admin

router = APIRouter(tags=["4] Admin Management"])


def _add_cta_already_exists_message(data: dict) -> str | None:
    """Add CTA duplicate message: ``{name} already exists`` (per item)."""
    if data["created"] or data["failed"] or not data["existing"]:
        return None
    names = [
        str(item.get("name") or "").strip()
        for item in (data.get("already_existing") or [])
        if isinstance(item, dict)
    ]
    names = [name for name in names if name]
    if not names:
        return None
    return ", ".join(f"{name} already exists" for name in names)


def _bulk_create_api_response(
    *,
    entity: str,
    data: dict,
    operation: CatalogBulkOperationType | None = None,
) -> CatalogBulkApiResponse:
    """Build bulk-create response.

    ``?type=import``: ``status`` is ``False`` only when every uploaded row
    failed (nothing created or already existing). Mixed / success batches
    stay ``True`` (current import semantics).

    Add CTA (no ``type``): ``status`` is ``False`` when any row already
    exists or any row failed. Pure duplicates use ``{name} already exists``.
    """
    created = data["created"]
    existing = data["existing"]
    failed = data["failed"]
    is_import = operation == CatalogBulkOperationType.IMPORT
    if not is_import:
        message = _add_cta_already_exists_message(data) or services.build_bulk_create_message(
            entity=entity,
            created=created,
            existing=existing,
            failed=failed,
        )
        ok = existing == 0 and failed == 0
    else:
        message = services.build_bulk_create_message(
            entity=entity,
            created=created,
            existing=existing,
            failed=failed,
        )
        all_failed = created == 0 and existing == 0 and failed > 0
        ok = not all_failed
    return CatalogBulkApiResponse(status=ok, message=message, data=data)


@router.post(
    "/admin/major",
    response_model=CatalogBulkApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_majors(
    payload: CatalogBulkRequest,
    type: CatalogBulkOperationType | None = Query(
        None,
        description="Omit for Add CTA. Pass `import` for bulk-import response semantics.",
    ),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> CatalogBulkApiResponse:
    _ = current_user
    data = await services.bulk_create_majors(payload.items, db)
    return _bulk_create_api_response(entity="major", data=data, operation=type)


@router.post(
    "/admin/minor",
    response_model=CatalogBulkApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_minors(
    payload: CatalogBulkRequest,
    type: CatalogBulkOperationType | None = Query(
        None,
        description="Omit for Add CTA. Pass `import` for bulk-import response semantics.",
    ),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> CatalogBulkApiResponse:
    _ = current_user
    data = await services.bulk_create_minors(payload.items, db)
    return _bulk_create_api_response(entity="minor", data=data, operation=type)


@router.post(
    "/admin/academic-interest",
    response_model=CatalogBulkApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_academic_interests(
    payload: AcademicInterestBulkRequest,
    type: CatalogBulkOperationType | None = Query(
        None,
        description="Omit for Add CTA. Pass `import` for bulk-import response semantics.",
    ),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> CatalogBulkApiResponse:
    _ = current_user
    data = await services.bulk_create_academic_interests(payload.items, db)
    return _bulk_create_api_response(entity="interest", data=data, operation=type)


@router.post(
    "/admin/university",
    response_model=CatalogBulkApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_universities(
    payload: UniversityBulkRequest,
    type: CatalogBulkOperationType | None = Query(
        None,
        description="Omit for Add CTA. Pass `import` for bulk-import response semantics.",
    ),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> CatalogBulkApiResponse:
    _ = current_user
    data = await services.bulk_create_universities(payload.items, db)
    return _bulk_create_api_response(entity="university", data=data, operation=type)


@router.post(
    "/admin/country",
    response_model=CatalogBulkApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_countries(
    payload: CountryBulkRequest,
    type: CatalogBulkOperationType | None = Query(
        None,
        description="Omit for Add CTA. Pass `import` for bulk-import response semantics.",
    ),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> CatalogBulkApiResponse:
    _ = current_user
    data = await services.bulk_create_countries(payload.items, db)
    return _bulk_create_api_response(entity="country", data=data, operation=type)


@router.delete("/admin/academics", response_model=ApiResponse)
async def admin_soft_delete_catalog(
    payload: AcademicCatalogSoftDeleteRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    _ = current_user
    data = await services.soft_delete_catalog_record(payload.type, payload.id, db)
    return ApiResponse(message="Academic catalog record deactivated successfully", data=data)


@router.patch("/admin/academics", response_model=CatalogBulkApiResponse)
async def admin_patch_catalog(
    payload: AcademicCatalogPatchRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> CatalogBulkApiResponse:
    _ = current_user
    data = await services.patch_catalog_record(payload.type, payload.id, payload.data, db)
    message = f"{payload.type.value.replace('_', ' ').title()} updated successfully"
    return CatalogBulkApiResponse(status=True, message=message, data=data)
