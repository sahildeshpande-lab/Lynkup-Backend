from __future__ import annotations

from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request, status, Form, UploadFile, File, BackgroundTasks
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import EmailStr
from core.database.session import get_session
from core.security.auth import (
    get_current_user_or_superadmin,
)
from apps.accounts.db_models import User
from apps.administration.dependencies import (
    require_admin_signed_request,
    require_signed_admin,
    require_signed_moderator,
    require_signed_moderator_or_viewer,
    require_signed_superadmin,
)
from apps.administration.services.signing_service import (
    register_pending_signing_key,
    revoke_signing_key,
)

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
    AdminSigningKeyRegisterRequest,
    AdminSigningKeyRevokeRequest,
    ChangePasswordRequest,
    AdminForgotPasswordRequest,
    AdminResetPasswordRequest,
    AdminPublishPostRequest,
    FeatureFlagCreateRequest,
    FeatureFlagUpdateRequest,
    TemplateCreate,
    TemplateUpdate,
    TemplateResponse,
    TemplateListItem,
)

from apps.accounts.schemas import EmailSignupRequest, RefreshTokenRequest, AdminAuthResponse
from apps.profiles.schemas import CompletenessWeightsUpdateRequest, UpdateProfileRequest
from apps.invitations.schemas import SoftDeleteInvitationRequest
from common.enums import (
    AdminActivityLogOrder,
    AdminActivityLogRole,
    AdminActivityLogSort,
    AdminInvitationListStatus,
    ReviewedPostOrder,
    ReviewedPostSort,
    StaffListStatus,
    UserListStatus,
)


router = APIRouter(tags=["4] Admin Management"])



@router.post("/auth/admin/signing-keys/register", response_model=ApiResponse)
async def admin_register_signing_key(
    payload: AdminSigningKeyRegisterRequest,
    request: Request,
) -> ApiResponse:
    """Pre-login public-key registration for Web Admin RSA request signing.

    Does not accept user_id. The key is activated only after successful admin login
    that includes the returned keyId.
    """
    return await register_pending_signing_key(payload.publicKey, request=request)


@router.post("/auth/admin/login", response_model=AdminAuthResponse)
async def admin_signin(
    payload: AdminLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> AdminAuthResponse:
    return await services.admin_signin(payload, db, request=request)


@router.post("/auth/admin/logout", response_model=ApiResponse)
async def admin_logout(
    request: Request,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin_signed_request),
) -> ApiResponse:
    """Revoke the current admin session and any RSA signing keys bound to it."""
    return await services.admin_logout(current_user, db, request=request)


@router.post("/auth/admin/signup", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def admin_signup(
    payload: AdminSignupRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_signed_superadmin),
) -> ApiResponse:
    """Create staff account. Requires signed request + superadmin."""
    return await services.admin_signup(payload, db, current_user=current_user)


@router.post("/auth/admin/signing-keys/revoke", response_model=ApiResponse)
async def admin_revoke_signing_key(
    payload: AdminSigningKeyRevokeRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin_signed_request),
) -> ApiResponse:
    return await revoke_signing_key(db, current_user, payload.keyId, request=request)


@router.get("/me", response_model=ApiResponse)
async def admin_me(
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin_signed_request),
):
    """Pilot RSA-signed Web Admin endpoint (JWT + request signature required)."""
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
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    return await services.admin_forgot_password(payload, db, request=request)


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
    current_user=Depends(require_admin_signed_request),
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
    status: UserListStatus | None = Query(
        default=None,
        description="Filter users by status: Pending, Active, Suspended, Banned, Deleted",
    ),
    is_alumni: bool | None = Query(
        default=None,
        description="Filter users by alumni status (true = alumni only, false = non-alumni only)",
    ),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(
        message="users listed",
        data=await services.list_users(
            page,
            pageSize,
            db,
            search=search,
            status=status,
            is_alumni=is_alumni,
        ),
    )


@router.get("/export", response_model=ApiResponse)
async def export_users(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(message="users exported", data=await services.export_users(page, pageSize, db))


@router.get("/moderators", response_model=ApiResponse)
async def list_moderators(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    search: str | None = Query(default=None, description="Search across university, name, or email"),
    status: StaffListStatus | None = Query(
        default=None,
        description="Filter staff by status: Active, Deleted",
    ),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(
        message="moderators listed",
        data=await services.list_moderators(page, pageSize, db, search=search, status=status)
    )


@router.get("/viewers", response_model=ApiResponse)
async def list_viewers(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    search: str | None = Query(default=None, description="Search across university, name, or email"),
    status: StaffListStatus | None = Query(
        default=None,
        description="Filter staff by status: Active, Deleted",
    ),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(
        message="viewers listed",
        data=await services.list_viewer(page, pageSize, db, search=search, status=status)
    )



@router.get("/admin/activity-logs", response_model=ApiResponse)
async def list_admin_activity_logs(
    module: str | None = Query(
        default=None,
        description=(
            "Filter logs by module, e.g. post, user, report, feature_flag, "
            "notification_campaign, bulk_email, invitation, profile, "
            "profile_completeness, moderation_threshold, recommendation, "
            "recommendation_settings, export."
        ),
    ),
    role: AdminActivityLogRole | None = Query(
        default=None,
        description="Filter logs by actor role. superadmin or moderator.",
    ),
    moderator_id: UUID | None = Query(
        default=None,
        description="Filter logs by actor (moderator or superadmin) user ID.",
    ),
    search: str | None = Query(
        default=None,
        description="Search by user_name or log description.",
    ),
    sort: AdminActivityLogSort = Query(
        default=AdminActivityLogSort.created_at,
        description=(
            "Sort column. created_at (default), module, action, or updated_at."
        ),
    ),
    order: AdminActivityLogOrder = Query(
        default=AdminActivityLogOrder.desc,
        description="Sort direction. desc = newest/Z-A first (default); asc = oldest/A-Z first.",
    ),
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator),
) -> ApiResponse:
    _ = current_user
    data = await services.list_admin_activity_logs_service(
        db,
        module=module,
        role=role,
        moderator_id=moderator_id,
        search=search,
        sort=sort,
        order=order,
        page=page,
        page_size=pageSize,
    )
    return ApiResponse(message="Activity logs retrieved successfully", data=data)


@router.post("/admin/users", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def create_user_by_admin(
    payload: AdminUserCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    return await services.admin_create_user(
        payload,
        db,
        background_tasks,
        actor_user_id=current_user.id,
        actor_role=current_user.role,
    )


@router.get("/users/{userId:uuid}", response_model=ApiResponse)
async def get_user_by_admin(
    userId: UUID,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(message="user fetched", data=await services.admin_get_user(userId, db))


@router.delete("/users/", response_model=ApiResponse)
async def delete_users_by_admin(
    payload: AdminDeleteUsersRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_superadmin),
) -> ApiResponse:
    data = await services.admin_delete_users(
        payload.userIds,
        payload.role,
        db,
        actor_user_id=current_user.id,
        actor_role=current_user.role,
    )
    if not data["deleted_users"]:
        return ApiResponse(status=True, message="No user found", data=data)
    return ApiResponse(message="users deletion scheduled", data=data)


@router.patch("/users/{userId}/status", response_model=ApiResponse)
async def update_user_status_by_admin(
    userId: UUID,
    payload: AdminUserStatusRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator),
) -> ApiResponse:
    return ApiResponse(
        message=f"user {payload.status.value} by admin",
        data=await services.admin_update_user_status(
            str(userId),
            payload.status,
            db,
            moderator_id=current_user.id,
            comment=payload.note,
            actor_role=current_user.role,
        )
    )

@router.patch(
    "/update-profile",
    response_model=ApiResponse
)
async def edit_profile(
    payload: AdminEditProfileRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_admin),
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
    current_user: User = Depends(require_signed_admin),
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
    current_user=Depends(require_signed_superadmin),
) -> ApiResponse:
    from apps.profiles import services as profiles_services
    data = await profiles_services.update_completeness_weights(
        payload,
        db,
        actor_user_id=current_user.id,
        actor_role=current_user.role,
    )
    return ApiResponse(message="Completeness weights updated", data=data)


@router.patch("/updateuserprofile", response_model=ApiResponse)
async def update_user_profile(
    payload: UpdateProfileRequest,
    id: UUID = Query(...),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_superadmin),
) -> ApiResponse:
    from apps.profiles import services as profiles_services
    data = await profiles_services.update_user_profile_by_admin_service(
        id,
        payload,
        db,
        actor_user_id=current_user.id,
        actor_role=current_user.role,
    )
    return ApiResponse(message="Profile updated successfully", data=data)


@router.get("/posts/processing", response_model=ApiResponse)
async def list_processing_posts(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    moderator_id: UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
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
    search: str | None = Query(
        default=None,
        description="Search author first name, last name, or post content",
    ),
    sort: ReviewedPostSort | None = Query(
        default=None,
        description=(
            "Sort column. created_at for published / student posts / rejected "
            "(default). updated_at for flagged (last student edit; default when "
            "status=flagged). latest_post is an alias of updated_at."
        ),
    ),
    order: ReviewedPostOrder = Query(
        default=ReviewedPostOrder.desc,
        description="Sort direction. desc = newest/latest first (default); asc = oldest first.",
    ),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
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
        search=search,
        sort=sort,
        order=order,
    )
    return ApiResponse(message="Posts fetched successfully", data=data)



@router.get("/admin/invitations", response_model=ApiResponse)
async def admin_list_invitations(
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    search: str | None = Query(
        default=None,
        description="Search by invitation code or inviter user name",
    ),
    status: AdminInvitationListStatus | None = Query(
        default=None,
        description=(
            "Filter by invitation status: Active, Redeemed, Deleted, Expired"
        ),
    ),
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_admin),
) -> ApiResponse:
    """List invitation codes (including expired, converted, and soft-deleted)."""
    from apps.invitations.services import get_all_invitations

    return await get_all_invitations(
        db,
        page=page,
        page_size=pageSize,
        search=search,
        status=status.value if status is not None else None,
    )


@router.delete("/admin/invitations", response_model=ApiResponse)
async def admin_soft_delete_invitation(
    payload: SoftDeleteInvitationRequest,
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_signed_admin),
) -> ApiResponse:
    """Soft-delete an invitation code. Preserves the row for audit history."""
    from apps.invitations.services import soft_delete_invitation

    return await soft_delete_invitation(
        db,
        code=payload.code,
        admin_user_id=current_user.id,
        actor_role=current_user.role,
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
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(
        message="Feature flag updated successfully",
        data=await services.update_feature_flag(
            payload,
            db,
            actor_user_id=current_user.id,
            actor_role=current_user.role,
        ),
    )


@router.post(
    "/admin/feature-flags",
    response_model=ApiResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_feature_flag(
    payload: FeatureFlagCreateRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    return ApiResponse(
        message="Feature flag created successfully",
        data=await services.create_feature_flag(
            payload,
            db,
            actor_user_id=current_user.id,
            actor_role=current_user.role,
        ),
    )


@router.delete("/admin/feature-flags/{flag_id}", response_model=ApiResponse)
async def delete_admin_feature_flag(
    flag_id: UUID,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    """Hard-delete a feature flag by id."""
    return ApiResponse(
        message="Feature flag deleted successfully",
        data=await services.delete_feature_flag(
            flag_id,
            db,
            actor_user_id=current_user.id,
            actor_role=current_user.role,
        ),
    )


@router.patch("/admin/posts/reviewed", response_model=ApiResponse)
async def admin_publish_or_flag_post(
    payload: AdminPublishPostRequest,
    db: AsyncSession = Depends(get_session),
    current_user=Depends(require_signed_moderator_or_viewer),
) -> ApiResponse:
    """
    Moderate a post by setting its state: published, flagged, rejected (soft delete),
    reinstate, or escalate.
    """
    from apps.feed.services import admin_publish_post_service, format_post_detail
    result = await admin_publish_post_service(
        post_id=payload.post_id,
        status=payload.status,
        admin_user_id=current_user.id,
        db=db,
        notes=payload.notes,
        actor_role=current_user.role,
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


from datetime import datetime, timezone
from sqlmodel import select
from apps.administration.db_models.template_db_model import Template
from common.exceptions import ApiError


@router.post("/admin/templates", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: TemplateCreate,
    db: AsyncSession = Depends(get_session),
    current_admin: User = Depends(require_signed_admin),
) -> ApiResponse:
    """Create a new email template."""
    from apps.administration.services.admin_activity_log_service import (
        create_admin_activity_log,
        format_email_template_activity_description,
    )

    from apps.administration.services.template_service import validate_template_placeholders

    stmt = select(Template).where(Template.name == payload.name)
    existing = (await db.execute(stmt)).scalars().first()
    if existing:
        raise ApiError(f"Email template '{payload.name}' already exists")

    validate_template_placeholders(
        payload.name,
        subject=payload.subject,
        body_html=payload.body_html,
    )

    status_value = payload.status.value if hasattr(payload.status, "value") else str(payload.status)
    template = Template(
        name=payload.name,
        subject=payload.subject,
        body_html=payload.body_html,
        status=status_value,
        updated_by=current_admin.id,
    )
    db.add(template)
    await db.flush()
    await create_admin_activity_log(
        db,
        user_id=current_admin.id,
        role=current_admin.role,
        action="create",
        module="email_template",
        record_id=template.id,
        description=format_email_template_activity_description("created", template.name),
        metadata={
            "old": None,
            "new": {
                "name": template.name,
                "subject": template.subject,
                "status": template.status,
            },
        },
    )
    await db.commit()
    await db.refresh(template)
    return ApiResponse(
        message="Template created successfully",
        data=TemplateResponse.model_validate(template),
    )


@router.get("/admin/templates", response_model=ApiResponse)
async def get_templates(
    name: str | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
    current_admin: User = Depends(require_signed_admin),
) -> ApiResponse:
    """List templates or get a specific template by name."""
    if name:
        stmt = select(Template).where(Template.name == name)
        template = (await db.execute(stmt)).scalars().first()
        if not template:
            raise ApiError(f"Email template '{name}' not found")
        return ApiResponse(
            message="Template retrieved successfully",
            data=TemplateResponse.model_validate(template),
        )

    stmt = select(Template).order_by(Template.name.asc())
    templates = (await db.execute(stmt)).scalars().all()
    return ApiResponse(
        message="Templates retrieved successfully",
        data=[TemplateListItem.model_validate(t) for t in templates],
    )


@router.patch("/admin/templates", response_model=ApiResponse)
async def update_template(
    payload: TemplateUpdate,
    db: AsyncSession = Depends(get_session),
    current_admin: User = Depends(require_signed_admin),
) -> ApiResponse:
    """Update template subject, body_html, or status."""
    from apps.administration.services.admin_activity_log_service import (
        create_admin_activity_log,
        format_email_template_activity_description,
    )

    from apps.administration.services.template_service import validate_template_placeholders

    stmt = select(Template).where(Template.id == payload.template_id)
    template = (await db.execute(stmt)).scalars().first()
    if not template:
        raise ApiError(f"Template with id '{payload.template_id}' not found")

    old_snapshot = {
        "name": template.name,
        "subject": template.subject,
        "status": template.status,
        "body_html": template.body_html,
    }

    next_subject = payload.subject if payload.subject is not None else template.subject
    next_body_html = payload.body_html if payload.body_html is not None else template.body_html
    if payload.subject is not None or payload.body_html is not None:
        validate_template_placeholders(
            template.name,
            subject=next_subject,
            body_html=next_body_html,
        )

    if payload.subject is not None:
        template.subject = payload.subject
    if payload.body_html is not None:
        template.body_html = payload.body_html
    if payload.status is not None:
        template.status = payload.status.value if hasattr(payload.status, "value") else str(payload.status)

    template.updated_by = current_admin.id
    template.updated_at = datetime.now(timezone.utc)

    db.add(template)
    await create_admin_activity_log(
        db,
        user_id=current_admin.id,
        role=current_admin.role,
        action="update",
        module="email_template",
        record_id=template.id,
        description=format_email_template_activity_description("updated", template.name),
        metadata={
            "old": old_snapshot,
            "new": {
                "name": template.name,
                "subject": template.subject,
                "status": template.status,
                "body_html": template.body_html,
            },
        },
    )
    await db.commit()
    await db.refresh(template)
    return ApiResponse(
        message="Template updated successfully",
        data=TemplateResponse.model_validate(template),
    )


@router.delete("/admin/templates/{template_id}", response_model=ApiResponse)
async def delete_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_session),
    current_admin: User = Depends(require_signed_admin),
) -> ApiResponse:
    """Delete a template by id."""
    from apps.administration.services.admin_activity_log_service import (
        create_admin_activity_log,
        format_email_template_activity_description,
    )

    stmt = select(Template).where(Template.id == template_id)
    template = (await db.execute(stmt)).scalars().first()
    if not template:
        raise ApiError(f"Template with id '{template_id}' not found")

    template_name = template.name
    template_id_value = template.id
    old_snapshot = {
        "name": template.name,
        "subject": template.subject,
        "status": template.status,
    }
    await create_admin_activity_log(
        db,
        user_id=current_admin.id,
        role=current_admin.role,
        action="delete",
        module="email_template",
        record_id=template_id_value,
        description=format_email_template_activity_description("deleted", template_name),
        metadata={"old": old_snapshot, "new": None},
    )
    await db.delete(template)
    await db.commit()
    return ApiResponse(
        message="Template deleted successfully",
    )


@router.post("/admin/graduation/runcron", response_model=ApiResponse, status_code=202)
async def run_graduation_email_cron(
    current_admin: User = Depends(require_signed_admin),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Queue graduation completion email delivery for a Celery worker.

    Only queues and delivers the graduation email template; does not run
    connection reminders or other transactional producers.
    """
    from apps.administration.services.admin_activity_log_service import (
        create_admin_activity_log,
    )
    from core.celery_worker.config import CeleryTaskQueue
    from core.jobs.publishing import publish_admin_task

    task_id = await publish_admin_task(
        "kampulynk.graduation.tick",
        CeleryTaskQueue.TRANSACTIONAL_QUEUE,
    )
    await create_admin_activity_log(
        db,
        user_id=current_admin.id,
        role=current_admin.role,
        action="queue",
        module="graduation_email",
        record_id=None,
        description="queued graduation email delivery",
        metadata={"task_id": task_id},
        commit=True,
    )
    return ApiResponse(
        message="Graduation email delivery queued.",
        data={"task_id": task_id, "status": "queued"},
    )


