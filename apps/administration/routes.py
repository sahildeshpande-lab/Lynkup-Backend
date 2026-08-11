from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends, Query, status, Form, UploadFile, File, BackgroundTasks
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import EmailStr
from core.database.session import get_session
from core.security.auth import (
    get_current_superadmin,
    get_current_admin,
    get_current_user_or_superadmin,
    get_current_moderator,
    get_current_moderator_or_viewer,
)
from apps.accounts.db_models import User

from . import services
from .schemas import (
    AdminUserActionRequest,
    AdminUserCreateRequest,
    AdminDeleteUsersRequest,
    AdminEditProfileRequest,
    AdminUserStatusRequest,
    ApiResponse,
    AdminLoginRequest,
    AdminSignupRequest,
    ChangePasswordRequest,
    AdminForgotPasswordRequest,
    AdminResetPasswordRequest,
    AdminPublishPostRequest,
    FeatureFlagCreateRequest,
    FeatureFlagUpdateRequest,
)
from apps.accounts.schemas import EmailSignupRequest, RefreshTokenRequest, AdminAuthResponse
from apps.profiles.schemas import CompletenessWeightsUpdateRequest, UpdateProfileRequest
from apps.invitations.schemas import SoftDeleteInvitationRequest


router = APIRouter(tags=["4] Admin Management"])



@router.post("/auth/admin/login", response_model=AdminAuthResponse)
async def admin_signin(
    payload: AdminLoginRequest,
    db: AsyncSession = Depends(get_session),
) -> AdminAuthResponse:
    return await services.admin_signin(payload, db)


@router.post("/auth/admin/signup", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def admin_signup(
    payload: AdminSignupRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    return await services.admin_signup(payload, db)

@router.get("/me",response_model=ApiResponse)
async def admin_me(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
):
    return await services.admin_me(
        current_user,
        db
    )


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


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_admin),
):
    return await services.change_password(
        payload,
        current_user,
        db
    )

@router.get("/users", response_model=ApiResponse)
async def list_users(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    search: str | None = Query(default=None, description="Search across university, name, or email"),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_moderator_or_viewer),
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
    current_user=Depends(get_current_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(message="users exported", data=await services.export_users(page, pageSize, db))


@router.get("/moderators", response_model=ApiResponse)
async def list_moderators(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    search: str | None = Query(default=None, description="Search across university, name, or email"),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(
        message="moderators listed",
        data=await services.list_moderators(page, pageSize, db, search=search)
    )


@router.get("/viewers", response_model=ApiResponse)
async def list_viewers(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    search: str | None = Query(default=None, description="Search across university, name, or email"),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(
        message="viewers listed",
        data=await services.list_viewer(page, pageSize, db, search=search)
    )



@router.post("/admin/users", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def create_user_by_admin(
    payload: AdminUserCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    _ = current_user
    return await services.admin_create_user(payload, db, background_tasks)


@router.get("/users/{userId:uuid}", response_model=ApiResponse)
async def get_user_by_admin(
    userId: UUID,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(message="user fetched", data=await services.admin_get_user(userId, db))


@router.delete("/users/", response_model=ApiResponse)
async def delete_users_by_admin(
    payload: AdminDeleteUsersRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    data = await services.admin_delete_users(payload.userIds, payload.role, db)
    if not data["deleted_users"]:
        return ApiResponse(status=True, message="No user found", data=data)
    return ApiResponse(message="users deletion scheduled", data=data)


@router.patch("/users/{userId}/status", response_model=ApiResponse)
async def update_user_status_by_admin(
    userId: UUID,
    payload: AdminUserStatusRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_moderator),
) -> ApiResponse:
    return ApiResponse(
        message=f"user {payload.status.value} by admin",
        data=await services.admin_update_user_status(
            str(userId),
            payload.status,
            db,
            moderator_id=current_user.id,
            comment=payload.note,
        )
    )

@router.patch(
    "/update-profile",
    response_model=ApiResponse
)
async def edit_profile(
    payload: AdminEditProfileRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_admin),
):
    return await services.admin_edit_profile(
        current_user.id,
        payload,
        db,
    )


@router.post("/admin/onboarding", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def admin_onboarding(
    university_id: str = Form(...),
    major: str = Form(...),
    minor: str | None = Form(default=None),
    education_level_id: int = Form(...),
    Bio: str = Form(...),
    academic_interests: str = Form(...),
    profile_photo: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    return ApiResponse(
        message="onboarding completed",
        data=await services.admin_complete_onboarding(
            current_user.id,
            Bio,
            major,
            minor,
            university_id,
            education_level_id,
            academic_interests,
            profile_photo,
            db,
        ),
    )




@router.patch("/update/completeness", response_model=ApiResponse)
async def update_completeness_weights(
    payload: CompletenessWeightsUpdateRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    from apps.profiles import services as profiles_services
    data = await profiles_services.update_completeness_weights(payload, db)
    return ApiResponse(message="Completeness weights updated", data=data)


@router.patch("/updateuserprofile", response_model=ApiResponse)
async def update_user_profile(
    payload: UpdateProfileRequest,
    id: UUID = Query(...),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    from apps.profiles import services as profiles_services
    data = await profiles_services.update_user_profile_by_admin_service(
        id,
        payload,
        db
    )
    return ApiResponse(message="Profile updated successfully", data=data)


@router.get("/posts/processing", response_model=ApiResponse)
async def list_processing_posts(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    moderator_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_moderator_or_viewer),
) -> ApiResponse:
    from apps.feed.services import list_processing_posts_service
    role = current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    target_moderator_id = moderator_id if role in ("superadmin", "viewer") else current_user.id
    data = await list_processing_posts_service(
        db,
        moderator_id=target_moderator_id,
        page=page,
        page_size=pageSize,
    )
    return ApiResponse(message="Processing posts fetched successfully", data=data)


@router.get("/admin/posts/reviewed", response_model=ApiResponse)
async def list_reviewed_posts(
    status: Literal["published", "flagged", "rejected", "reinstate", "escalate"] | None = Query(
        default=None,
        description=(
            "Filter reviewed posts by status: published, flagged, rejected, reinstate, escalate. "
            "published includes reinstate. flagged includes processing posts awaiting re-review."
        ),
    ),
    moderator_id: str | None = Query(
        default=None,
        description="Filter posts reviewed by a specific moderator",
    ),
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_moderator_or_viewer),
) -> ApiResponse:
    from apps.feed.services import list_reviewed_posts_by_state_service
    from common.exceptions import ApiError

    _ = current_user
    target_moderator_id = None
    if moderator_id is not None:
        try:
            target_moderator_id = UUID(moderator_id)
        except ValueError as exc:
            raise ApiError("Invalid moderator_id") from exc

    data = await list_reviewed_posts_by_state_service(
        db,
        moderator_id=target_moderator_id,
        status=status,
        page=page,
        page_size=pageSize,
        viewer_user_id=current_user.id,
    )
    return ApiResponse(message="Posts fetched successfully", data=data)



@router.get("/admin/invitations", response_model=ApiResponse)
async def admin_list_invitations(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_admin),
) -> ApiResponse:
    """List all invitation codes (including expired, deactivated, converted, soft-deleted)."""
    from apps.invitations.services import get_all_invitations

    return await get_all_invitations(db, page=page, page_size=pageSize)


@router.delete("/admin/invitations", response_model=ApiResponse)
async def admin_soft_delete_invitation(
    payload: SoftDeleteInvitationRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin),
) -> ApiResponse:
    """Soft-delete an invitation code. Preserves the row for audit history."""
    from apps.invitations.services import soft_delete_invitation

    return await soft_delete_invitation(
        db,
        code=payload.code,
        admin_user_id=current_user.id,
    )


@router.get("/feature-flags", response_model=ApiResponse)
async def list_feature_flags(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user_or_superadmin),
) -> ApiResponse:
    """Return all platform feature flags for authenticated app users and superadmins."""
    _ = current_user
    return ApiResponse(
        message="Feature flags fetched successfully",
        data=await services.list_feature_flags(db),
    )


@router.patch("/admin/feature-flags", response_model=ApiResponse)
async def patch_admin_feature_flag(
    payload: FeatureFlagUpdateRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    _ = current_user
    return ApiResponse(
        message="Feature flag updated successfully",
        data=await services.update_feature_flag(payload, db),
    )


@router.post(
    "/admin/feature-flags",
    response_model=ApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_feature_flag(
    payload: FeatureFlagCreateRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    _ = current_user
    return ApiResponse(
        message="Feature flag created successfully",
        data=await services.create_feature_flag(payload, db),
    )


@router.delete("/admin/feature-flags/{flag_id}", response_model=ApiResponse)
async def delete_admin_feature_flag(
    flag_id: UUID,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_superadmin),
) -> ApiResponse:
    """Hard-delete a feature flag by id."""
    _ = current_user
    return ApiResponse(
        message="Feature flag deleted successfully",
        data=await services.delete_feature_flag(flag_id, db),
    )


@router.patch("/admin/posts/reviewed", response_model=ApiResponse)
async def admin_publish_or_flag_post(
    payload: AdminPublishPostRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_moderator),
) -> ApiResponse:
    """
    Moderate a post by setting its state: published, flagged, rejected (hard delete),
    reinstate, or escalate.
    """
    from apps.feed.services import admin_publish_post_service, format_post_detail
    result = await admin_publish_post_service(
        post_id=payload.post_id,
        status=payload.status,
        admin_user_id=current_user.id,
        db=db,
        notes=payload.notes,
    )
    _status_messages = {
        "published": "Post published successfully",
        "flagged": "Post flagged successfully",
        "rejected": "Post rejected and deleted successfully",
        "reinstate": "Post reinstated successfully",
        "escalate": "Post escalated to senior admin successfully",
    }
    if payload.status == "rejected":
        return ApiResponse(
            status=True,
            message=_status_messages["rejected"],
            data=result,
        )
    return ApiResponse(
        status=True,
        message=_status_messages.get(payload.status, "Post updated successfully"),
        data=format_post_detail(result, viewer_user_id=current_user.id),
    )
