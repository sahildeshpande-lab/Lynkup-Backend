from __future__ import annotations

import inspect
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import get_session
from core.auth.dependencies import require_recent_auth
from core.security.auth import get_bearer_token, get_current_user, get_current_app_user
from core.security.mobile.dependencies import require_mobile_request_security
from apps.accounts.db_models import User
from apps.accounts.schemas import ChangeEmailRequest
from apps.accounts.services import change_email as change_email_service
from .schemas import (
    ApiResponse,
    OnboardingRequest,
    ProfileUpdateRequest,
    ProfileVisibilityRequest,
    ReportUserRequest,
    UpdateProfileMeRequest,
    UpdateProfileRequest,
)
from . import services

router = APIRouter(tags=["2] User Management"])


def _require_bearer_token(token: str | None = Depends(get_bearer_token)) -> str:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    return token


@router.get("/myprofile", response_model=ApiResponse)
async def get_my_profile(
    user_id: UUID | None = Query(default=None, description="Optional user id to fetch another user's profile"),
    current_user: User = Depends(require_mobile_request_security),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    service = services.get_my_profile_service
    if "target_user_id" in inspect.signature(service).parameters:
        data = await service(current_user, db, target_user_id=user_id)
    else:
        data = await service(current_user, db)
    return ApiResponse(
        message="Profile retrieved successfully",
        data=data,
    )


@router.get("/users/me/completeness", response_model=ApiResponse)
async def get_me_completeness(
    current_user: User = Depends(require_mobile_request_security),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    return ApiResponse(message="profile completeness fetched", data=await services.get_me_completeness(current_user.id, db))


@router.patch("/updateprofile", response_model=ApiResponse)
async def update_profile(
    payload: UpdateProfileRequest,
    current_user: User = Depends(require_mobile_request_security),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:

    data = await services.update_my_profile_service(
        user=current_user,
        payload=payload,
        db=db
    )

    return ApiResponse(
        message="Profile updated successfully",
        data=data
    )


@router.delete("/users/me/deletion", response_model=ApiResponse)
async def delete_me(
    current_user: User = Depends(require_mobile_request_security),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await services.delete_user_me(current_user, db)
    return ApiResponse(message="user deletion scheduled", data=data)


@router.patch("/users/me/email", response_model=ApiResponse)
async def change_email(
    payload: ChangeEmailRequest,
    current_user: User = Depends(require_mobile_request_security),
    _recent_auth: dict = Depends(require_recent_auth),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    return await change_email_service(current_user, payload, db)



@router.post("/users/onboarding", response_model=ApiResponse)
async def complete_onboarding(
    payload: OnboardingRequest,
    current_user: User = Depends(require_mobile_request_security),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await services.complete_onboarding(
        user=current_user,
        profile_photo_key=payload.profile_photo_key,
        banner_photo_key=payload.banner_photo_key,
        country_id=payload.country_id,
        university_id=payload.university_id,
        major=payload.major,
        minor=payload.minor,
        major_id=payload.major_id,
        minor_id=payload.minor_id,
        education_level_id=payload.education_level_id,
        bio=payload.bio,
        academic_interests=payload.academic_interests,
        invitation_code=payload.invitation_code,
        graduation_date=payload.graduationDate,
        db=db,
    )
    return ApiResponse(message="onboarding completed", data=data)


@router.patch("/profilevisibility", response_model=ApiResponse)
async def update_profile_visibility(
    payload: ProfileVisibilityRequest,
    current_user: User = Depends(require_mobile_request_security),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await services.update_profile_visibility_service(
        user=current_user,
        payload=payload,
        db=db
    )
    return ApiResponse(message="profile visibility updated", data=data)


@router.get("/users/{email}", response_model=ApiResponse)
def get_public_profile(email: str) -> ApiResponse:
    return ApiResponse(message="public profile fetched", data=services.get_public_profile(email))


