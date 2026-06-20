from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends, Query, status, Form, UploadFile, File
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import EmailStr
from core.database.session import get_session
from core.security.auth import get_current_superadmin, get_current_admin
from apps.accounts.db_models import User

from . import services
from .schemas import (
    AdminUserActionRequest,
    ApiResponse,
    AdminSignupRequest,
    AdminLoginRequest,
    AdminForgotPasswordRequest,
    AdminResetPasswordRequest,
)
from apps.accounts.schemas import EmailSignupRequest, RefreshTokenRequest, AdminAuthResponse
from apps.profiles.schemas import CompletenessWeightsUpdateRequest


router = APIRouter(tags=["4] Admin Management"])


@router.post("/auth/admin/signup", response_model=AdminAuthResponse, status_code=status.HTTP_201_CREATED)
async def admin_signup(
    payload: AdminSignupRequest,
    db: AsyncSession = Depends(get_session),
) -> AdminAuthResponse:
    return await services.admin_signup(payload, db)


@router.post("/admin/onboarding", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def admin_onboarding(
    profile_photo: UploadFile = File(...),
    university_id: str = Form(...),
    major: str = Form(...),
    minor: str | None = Form(None),
    education_level_id: int = Form(...),
    Bio: str = Form(...),
    academic_interests: str = Form(...),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    data = await services.admin_complete_onboarding(
        user_id=current_user.id,
        profile_photo=profile_photo,
        university_id=university_id,
        major=major,
        minor=minor,
        education_level_id=education_level_id,
        bio=Bio,
        academic_interests=academic_interests,
        db=db,
    )
    return ApiResponse(message="onboarding completed", data=data)


@router.post("/auth/admin/login", response_model=AdminAuthResponse)
async def admin_signin(
    payload: AdminLoginRequest,
    db: AsyncSession = Depends(get_session),
) -> AdminAuthResponse:
    return await services.admin_signin(payload, db)


@router.post("/auth/admin/token", response_model=ApiResponse)
async def admin_token(payload: RefreshTokenRequest, db: AsyncSession = Depends(get_session)) -> ApiResponse:
    return ApiResponse(message="token refreshed", data=await services.admin_token(payload, db))


@router.post("/forgot-password", response_model=ApiResponse)
async def forgot_password(
    payload: AdminForgotPasswordRequest,
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    return await services.admin_forgot_password(payload, db)


@router.post("/reset-password", response_model=ApiResponse)
async def reset_password(
    payload: AdminResetPasswordRequest,
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    return await services.admin_reset_password(payload, db)


@router.get("/users", response_model=ApiResponse)
async def list_users(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    search: str | None = Query(default=None, description="Search across university, name, or email"),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    return ApiResponse(
        message="users listed",
        data=await services.list_users(page, pageSize, db, search=search)
    )


@router.get("/export", response_model=ApiResponse)
async def export_users(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    return ApiResponse(message="users exported", data=await services.export_users(page, pageSize, db))


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


@router.delete("/users/{userId:uuid}", response_model=ApiResponse)
async def delete_user_by_admin(
    userId: UUID,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    return ApiResponse(message="user deletion scheduled", data=await services.admin_delete_user(userId, db))


# @router.post("/users/{userId:uuid}/suspend", response_model=ApiResponse)
# async def suspend_user_by_admin(
#     userId: UUID,
#     payload: AdminUserActionRequest,
#     db: AsyncSession = Depends(get_session),
#     current_user=Depends(get_current_superadmin),
# ) -> ApiResponse:
#     _ = userId
#     return ApiResponse(message="user suspended by admin", data=await services.admin_suspend_user(payload, db))


# @router.post("/users/{userId:uuid}/ban", response_model=ApiResponse)
# async def ban_user_by_admin(
#     userId: UUID,
#     payload: AdminUserActionRequest,
#     db: AsyncSession = Depends(get_session),
#     current_user=Depends(get_current_superadmin),
# ) -> ApiResponse:
#     _ = userId
#     return ApiResponse(message="user banned by admin", data=await services.admin_ban_user(payload, db))



@router.patch("/update/completeness", response_model=ApiResponse)
async def update_completeness_weights(
    payload: CompletenessWeightsUpdateRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    from apps.profiles import services as profiles_services
    data = await profiles_services.update_completeness_weights(payload, db)
    return ApiResponse(message="Completeness weights updated", data=data)
