from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import get_session
from core.security.auth import get_bearer_token, get_current_user
from core.auth.dependencies import require_recent_auth
from apps.accounts.db_models import User
from .schemas import (
    ApiResponse,
    ProfileUpdateRequest,
    ProfileVisibilityRequest,
    ReportUserRequest,
)
from . import services

router = APIRouter(tags=["2] User Management"])


def _require_bearer_token(token: str | None = Depends(get_bearer_token)) -> str:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    return token


@router.get("/users/me", response_model=ApiResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    return ApiResponse(message="current user fetched", data=await services.get_profile_me(current_user, db))


@router.get("/users/me/completeness", response_model=ApiResponse)
async def get_me_completeness(
    token: str = Depends(_require_bearer_token),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    return ApiResponse(message="profile completeness fetched", data=await services.get_me_completeness(token, db))


@router.patch("/users/me", response_model=ApiResponse)
async def update_profile(
    bio: str | None = Form(None),
    academic_interests: str | None = Form(None),
    profile_photo: UploadFile | None = File(None),
    banner_photo: UploadFile | None = File(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await services.update_profile_me_form(
        current_user=current_user,
        bio=bio,
        academic_interests=academic_interests,
        profile_photo=profile_photo,
        banner_photo=banner_photo,
        db=db
    )
    return ApiResponse(message="profile updated", data=data)


@router.delete("/users/me/deletion", response_model=ApiResponse, dependencies=[Depends(require_recent_auth)])
async def delete_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await services.delete_user_me(current_user, db)
    return ApiResponse(message="user deletion scheduled", data=data)



@router.post("/users/onboarding", response_model=ApiResponse)
async def complete_onboarding(
    profile_photo: UploadFile = File(...),
    university_id: str = Form(...),
    major: str = Form(...),
    minor: str | None = Form(None),
    education_level: str = Form(...),
    Bio: str = Form(...),
    academic_interests: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    data = await services.complete_onboarding(
        user=current_user,
        profile_photo=profile_photo,
        university_id=university_id,
        major=major,
        minor=minor,
        education_level=education_level,
        bio=Bio,
        academic_interests=academic_interests,
        db=db,
    )
    return ApiResponse(message="onboarding completed", data=data)


@router.patch("/users/me/visibility", response_model=ApiResponse)
def set_visibility(payload: ProfileVisibilityRequest) -> ApiResponse:
    return ApiResponse(message="profile visibility updated", data=services.update_visibility(payload))


@router.get("/users/{email}", response_model=ApiResponse)
def get_public_profile(email: str) -> ApiResponse:
    return ApiResponse(message="public profile fetched", data=services.get_public_profile(email))


# @router.post("/users/{userId}/follow", response_model=ApiResponse)
# def follow_user(userId: str) -> ApiResponse:
#     return ApiResponse(message="user followed", data=services.follow_user(userId))


# @router.delete("/users/{userId}/follow", response_model=ApiResponse)
# def unfollow_user(userId: str) -> ApiResponse:
#     return ApiResponse(message="user unfollowed", data=services.unfollow_user(userId))


# @router.post("/users/{userId}/block", response_model=ApiResponse)
# def block_user(userId: str) -> ApiResponse:
#     return ApiResponse(message="user blocked", data=services.block_user(userId))


# @router.delete("/users/{userId}/block", response_model=ApiResponse)
# def unblock_user(userId: str) -> ApiResponse:
#     return ApiResponse(message="user unblocked", data=services.unblock_user(userId))


# @router.patch("/users/{userId}/report", response_model=ApiResponse)
# def report_user(userId: str, payload: ReportUserRequest) -> ApiResponse:
#     return ApiResponse(message="user reported", data=services.report_user(userId, payload))


# @router.post("/users/{userId}/lynkup/request", response_model=ApiResponse)
# def request_lynkup(userId: str) -> ApiResponse:
#     return ApiResponse(message="lynkup requested", data=services.request_lynkup(userId))


# @router.post("/users/{userId}/lynkup/accept", response_model=ApiResponse)
# def accept_lynkup(userId: str) -> ApiResponse:
#     return ApiResponse(message="lynkup accepted", data=services.accept_lynkup(userId))


# @router.delete("/users/{userId}/lynkup", response_model=ApiResponse)
# def remove_lynkup(userId: str) -> ApiResponse:
#     return ApiResponse(message="lynkup removed", data=services.remove_lynkup(userId))





