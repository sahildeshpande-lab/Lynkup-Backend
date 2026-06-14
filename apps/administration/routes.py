from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.session import get_session
from core.security.auth import get_current_superadmin

from . import services
from .schemas import (
    AdminUserActionRequest,
    AdminUserUpdateRequest,
    ApiResponse,
)
from apps.accounts.schemas import EmailSignupRequest, LoginRequest, RefreshTokenRequest
from apps.profiles.schemas import EducationUpdateRequest, CompletenessWeightsUpdateRequest

router = APIRouter(tags=["4] Admin Management"])


@router.post("/auth/admin/signup", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def admin_signup(payload: EmailSignupRequest, db: AsyncSession = Depends(get_session)) -> ApiResponse:
    return await services.admin_signup(payload, db)


@router.post("/auth/admin/education", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def admin_education(payload: EducationUpdateRequest, db: AsyncSession = Depends(get_session)) -> ApiResponse:
    return ApiResponse(message="education saved", data=await services.admin_education(payload, db))


@router.post("/auth/admin/login", response_model=ApiResponse)
async def admin_signin(payload: LoginRequest, db: AsyncSession = Depends(get_session)) -> ApiResponse:
    return await services.admin_signin(payload, db)


@router.post("/auth/admin/token", response_model=ApiResponse)
async def admin_token(payload: RefreshTokenRequest, db: AsyncSession = Depends(get_session)) -> ApiResponse:
    return ApiResponse(message="token refreshed", data=await services.admin_token(payload, db))


@router.get("/users", response_model=ApiResponse)
async def list_users(
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    return ApiResponse(message="users listed", data=await services.list_users(page, pageSize, db))


@router.post("/users", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def create_user_by_admin(
    payload: EmailSignupRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    _ = current_user
    return await services.admin_create_user(payload, db)


@router.get("/users/{userId:uuid}", response_model=ApiResponse)
async def get_user_by_admin(
    userId: UUID,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    return ApiResponse(message="user fetched", data=await services.admin_get_user(userId, db))


@router.patch("/users/{userId:uuid}", response_model=ApiResponse)
async def update_user_by_admin(
    userId: UUID,
    payload: AdminUserUpdateRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    return ApiResponse(message="profile updated", data=await services.admin_update_user(userId, payload, db))


@router.delete("/users/{userId:uuid}", response_model=ApiResponse)
async def delete_user_by_admin(
    userId: UUID,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    return ApiResponse(message="user deletion scheduled", data=await services.admin_delete_user(userId, db))


@router.post("/users/{userId:uuid}/suspend", response_model=ApiResponse)
async def suspend_user_by_admin(
    userId: UUID,
    payload: AdminUserActionRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    _ = userId
    return ApiResponse(message="user suspended by admin", data=await services.admin_suspend_user(payload, db))


@router.post("/users/{userId:uuid}/ban", response_model=ApiResponse)
async def ban_user_by_admin(
    userId: UUID,
    payload: AdminUserActionRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    _ = userId
    return ApiResponse(message="user banned by admin", data=await services.admin_ban_user(payload, db))


@router.patch("/update/completeness", response_model=ApiResponse)
async def update_completeness_weights(
    payload: CompletenessWeightsUpdateRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    from apps.profiles import services as profiles_services
    data = await profiles_services.update_completeness_weights(payload, db)
    return ApiResponse(message="Completeness weights updated", data=data)
