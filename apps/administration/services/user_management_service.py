from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from fastapi import HTTPException, status, BackgroundTasks
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import and_, case, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from apps.accounts.db_models import User, UserRole, Role
from common.enums import (
    OnboardingStatus,
    UserStatus,
    UserListStatus,
    StaffListStatus,
    RegistrationType,
    format_user_status,
)
from common.pagination import build_paginated_response
from ..schemas import AdminEditProfileRequest, AdminUserStatus
from apps.accounts.schemas import ApiResponse
from apps.profiles.services import build_user_base_response
from apps.profiles.db_models import Profile
from apps.learningspotlight.schemas import (
    extract_recommended_cycle_name,
    extract_recommended_papers,
)
from apps.accounts.services.consent_service import (
    CONSENT_SOURCE_ADMIN_CREATION,
    save_current_consent,
)
from core.auth.services import create_firebase_user, delete_firebase_user
import secrets
import string
import logging

PASSWORD_HASHER = PasswordHash((BcryptHasher(),))
ADMIN_MANAGED_ROLES = ["user", "moderator", "viewer"]
logger = logging.getLogger(__name__)


def _admin_visible_users_clause():
    """Include active accounts and grace-period deleting accounts in admin lists."""
    return or_(
        User.is_deleted.is_(False),
        User.status == UserStatus.deleting,
    )


def _build_status_clause(status: UserListStatus | StaffListStatus | str | None):
    if status is None:
        return _admin_visible_users_clause()

    val = status.value.lower() if hasattr(status, "value") else str(status).strip().lower()

    if val == "deleted":
        return or_(User.is_deleted.is_(True), User.status == UserStatus.deleting)
    elif val == "active":
        return and_(User.is_deleted.is_(False), User.status == UserStatus.active)
    elif val == "pending":
        return and_(User.is_deleted.is_(False), User.status == UserStatus.pending)
    elif val == "suspended":
        return and_(User.is_deleted.is_(False), User.status == UserStatus.suspended)
    elif val == "banned":
        return and_(User.is_deleted.is_(False), User.status == UserStatus.banned)
    elif val == "deleting":
        return User.status == UserStatus.deleting
    else:
        return _admin_visible_users_clause()


def _build_alumni_clause(is_alumni: bool | None):
    """Filter list results by persisted profile alumni flag."""
    if is_alumni is None:
        return True
    if is_alumni:
        return Profile.is_alumni.is_(True)
    return or_(Profile.is_alumni.is_(False), Profile.is_alumni.is_(None))


def _utc_today(today: date | datetime | None = None) -> date:
    if today is None:
        return datetime.now(timezone.utc).date()
    if isinstance(today, datetime):
        if today.tzinfo is None:
            return today.replace(tzinfo=timezone.utc).date()
        return today.astimezone(timezone.utc).date()
    return today


def _learning_spotlight_recommended_today_bounds(
    today: date | datetime | None = None,
) -> tuple[datetime, datetime]:
    """UTC [start, end) window for “recommended today” checks."""
    day = _utc_today(today)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _is_learning_spotlight_recommended_today(
    recommended_at: datetime | None,
    *,
    today: date | datetime | None = None,
) -> bool:
    """True when ``learning_spotlight_updated_at`` falls on the UTC calendar day."""
    if recommended_at is None:
        return False
    if recommended_at.tzinfo is None:
        recommended_at = recommended_at.replace(tzinfo=timezone.utc)
    else:
        recommended_at = recommended_at.astimezone(timezone.utc)
    return recommended_at.date() == _utc_today(today)


def _learning_spotlight_recommended_today_sql(
    today: date | datetime | None = None,
):
    """SQL expression: spotlight exists and was recommended today (UTC)."""
    start, end = _learning_spotlight_recommended_today_bounds(today)
    return and_(
        Profile.learning_spotlight.is_not(None),
        Profile.learning_spotlight_updated_at.is_not(None),
        Profile.learning_spotlight_updated_at >= start,
        Profile.learning_spotlight_updated_at < end,
    )


def _build_learning_spotlight_clause(is_learning_spotlight_recommended: bool | None):
    """Filter by whether Learning Spotlight was recommended today (UTC)."""
    if is_learning_spotlight_recommended is None:
        return True
    recommended_today = _learning_spotlight_recommended_today_sql()
    if is_learning_spotlight_recommended:
        return recommended_today
    return not_(recommended_today)


def _generate_temporary_password() -> str:
    alphabet = string.ascii_letters + string.digits + "@#$%"
    temp_password = "".join(
        secrets.choice(alphabet)
        for _ in range(12)
    )
    return temp_password

def _coerce_uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))

async def _fetch_users_with_details(
    db: AsyncSession,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
    role: str | None = None,
    status: UserListStatus | StaffListStatus | str | None = None,
    is_alumni: bool | None = None,
    is_learning_spotlight_recommended: bool | None = None,
    include_learning_spotlight_fields: bool = False,
) -> list[dict]:
    from apps.profiles.db_models.university_db_model import University
    from apps.profiles.db_models.country_db_model import Country
    from apps.profiles.db_models.academic_interests_db_model import AcademicInterest

    status_clause = _build_status_clause(status)
    spotlight_clause = _build_learning_spotlight_clause(is_learning_spotlight_recommended)

    select_columns: list = [
        User,
        Profile,
        University.name.label("university_name"),
        Country.name.label("country_name"),
    ]
    if include_learning_spotlight_fields:
        recommended_today_expr = _learning_spotlight_recommended_today_sql()
        select_columns.extend(
            [
                Profile.user_id.label("spotlight_user_id"),
                Profile.extracted_keywords.label("extracted_keywords"),
                case(
                    (recommended_today_expr, True),
                    else_=False,
                ).label("is_learning_spotlight_recommended"),
                Profile.learning_spotlight_updated_at.label(
                    "learning_spotlight_recommended_at"
                ),
            ]
        )

    stmt = (
        select(*select_columns)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .outerjoin(Country, Country.id == Profile.country_id)
        .where(
            status_clause,
            Role.name == role,
            _build_alumni_clause(is_alumni),
            spotlight_clause,
        )
    )
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )
    stmt = (
        stmt.options(selectinload(User.roles))
        .order_by(User.created_at.desc())
    )
    if page is not None and page_size is not None:
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    results = (await db.execute(stmt)).all()

    # Pre-fetch academic interests in bulk to avoid N+1 query
    interest_ids = set()
    for row in results:
        profile = row.Profile
        if profile and profile.profile_interests_id:
            for value in profile.profile_interests_id:
                if value is None:
                    continue
                try:
                    interest_ids.add(int(value))
                except (TypeError, ValueError):
                    pass

    interest_name_map = {}
    if interest_ids:
        interest_stmt = select(AcademicInterest.id, AcademicInterest.name).where(
            AcademicInterest.id.in_(list(interest_ids))
        )
        interest_rows = (await db.execute(interest_stmt)).all()
        interest_name_map = {r.id: r.name for r in interest_rows}

    from apps.moderation.repositories import get_latest_comments_by_entity_ids
    from common.enums import ReportEntityType

    user_ids = [row.User.id for row in results]
    moderation_notes_map = await get_latest_comments_by_entity_ids(
        db,
        entity_type=ReportEntityType.user,
        entity_ids=user_ids,
    )

    items = []
    for row in results:
        user = row.User
        profile = row.Profile
        univ_name = row.university_name
        cntry_name = row.country_name

        # Map interests for this profile
        profile_interests = []
        if profile and profile.profile_interests_id:
            for value in profile.profile_interests_id:
                if value is None:
                    continue
                try:
                    interest_id = int(value)
                except (TypeError, ValueError):
                    continue
                if interest_id in interest_name_map:
                    profile_interests.append(interest_name_map[interest_id])

        user_data = await build_user_base_response(
            user,
            profile,
            db,
            university_name=univ_name,
            country_name=cntry_name,
            interests=profile_interests,
        )
        user_data["moderation_notes"] = moderation_notes_map.get(user.id)

        if include_learning_spotlight_fields:
            if hasattr(row, "spotlight_user_id") and row.spotlight_user_id is not None:
                spotlight_user_id = row.spotlight_user_id
            elif profile is not None and profile.user_id is not None:
                spotlight_user_id = profile.user_id
            else:
                spotlight_user_id = user.id
            user_data["user_id"] = str(spotlight_user_id)

            if hasattr(row, "extracted_keywords"):
                extracted_keywords = row.extracted_keywords
            elif profile is not None:
                extracted_keywords = profile.extracted_keywords
            else:
                extracted_keywords = None
            user_data["extracted_keywords"] = extracted_keywords

            if hasattr(row, "is_learning_spotlight_recommended"):
                is_recommended = bool(row.is_learning_spotlight_recommended)
            else:
                recommended_at_for_flag = (
                    profile.learning_spotlight_updated_at
                    if profile is not None
                    else None
                )
                is_recommended = (
                    profile is not None
                    and profile.learning_spotlight is not None
                    and _is_learning_spotlight_recommended_today(recommended_at_for_flag)
                )
            user_data["is_learning_spotlight_recommended"] = is_recommended

            if hasattr(row, "learning_spotlight_recommended_at"):
                recommended_at = row.learning_spotlight_recommended_at
            elif profile is not None:
                recommended_at = profile.learning_spotlight_updated_at
            else:
                recommended_at = None
            if isinstance(recommended_at, datetime):
                recommended_at = recommended_at.isoformat()
            user_data["learning_spotlight_recommended_at"] = recommended_at

            spotlight_payload = (
                profile.learning_spotlight if profile is not None else None
            )
            user_data["recommended_cycle_name"] = extract_recommended_cycle_name(
                spotlight_payload
            )
            paper_ids, paper_titles = extract_recommended_papers(spotlight_payload)
            user_data["paper_id"] = paper_ids
            user_data["paper_title"] = paper_titles

        items.append(user_data)

    return items

async def list_users(
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    search: str | None = None,
    status: UserListStatus | str | None = None,
    is_alumni: bool | None = None,
) -> dict:
    from apps.profiles.db_models.university_db_model import University

    status_clause = _build_status_clause(status)
    count_stmt = (
        select(func.count(User.id))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(status_clause, Role.name == "user", _build_alumni_clause(is_alumni))
    )
    if search:
        pattern = f"%{search}%"
        count_stmt = count_stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )
    total_items = int((await db.execute(count_stmt)).scalar_one())
    items = await _fetch_users_with_details(
        db,
        page,
        page_size,
        search=search,
        role="user",
        status=status,
        is_alumni=is_alumni,
        include_learning_spotlight_fields=False,
    )
    if page is None:
        page = 1
    if page_size is None:
        page_size = len(items)

    return build_paginated_response(items, page, page_size, total_items).model_dump()


async def list_learning_spotlight_users(
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    search: str | None = None,
    status: UserListStatus | str | None = None,
    is_learning_spotlight_recommended: bool | None = None,
) -> dict:
    """List users for admin Learning Spotlight review with spotlight fields."""
    from apps.profiles.db_models.university_db_model import University

    status_clause = _build_status_clause(status)
    spotlight_clause = _build_learning_spotlight_clause(is_learning_spotlight_recommended)
    count_stmt = (
        select(func.count(User.id))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(status_clause, Role.name == "user", spotlight_clause)
    )
    if search:
        pattern = f"%{search}%"
        count_stmt = count_stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )
    total_items = int((await db.execute(count_stmt)).scalar_one())
    items = await _fetch_users_with_details(
        db,
        page,
        page_size,
        search=search,
        role="user",
        status=status,
        is_learning_spotlight_recommended=is_learning_spotlight_recommended,
        include_learning_spotlight_fields=True,
    )
    if page is None:
        page = 1
    if page_size is None:
        page_size = len(items)

    return build_paginated_response(items, page, page_size, total_items).model_dump()

async def list_moderators(
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    search: str | None = None,
    status: StaffListStatus | str | None = None,
) -> dict:
    from apps.profiles.db_models.university_db_model import University

    status_clause = _build_status_clause(status)
    count_stmt = (
        select(func.count(User.id))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(status_clause, Role.name == "moderator")
    )
    if search:
        pattern = f"%{search}%"
        count_stmt = count_stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )
    total_items = int((await db.execute(count_stmt)).scalar_one())
    items = await _fetch_users_with_details(db, page, page_size, search=search, role="moderator", status=status)
    if page is None:
        page = 1
    if page_size is None:
        page_size = len(items)

    return build_paginated_response(items, page, page_size, total_items).model_dump()

async def list_viewer(
    page: int | None,
    page_size: int | None,
    db: AsyncSession,
    search: str | None = None,
    status: StaffListStatus | str | None = None,
) -> dict:
    from apps.profiles.db_models.university_db_model import University

    status_clause = _build_status_clause(status)
    count_stmt = (
        select(func.count(User.id))
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .outerjoin(Profile, Profile.user_id == User.id)
        .outerjoin(University, University.id == Profile.university_id)
        .where(status_clause, Role.name == "viewer")
    )
    if search:
        pattern = f"%{search}%"
        count_stmt = count_stmt.where(
            (University.name.ilike(pattern)) |
            (Profile.first_name.ilike(pattern)) |
            (Profile.last_name.ilike(pattern)) |
            (func.concat(Profile.first_name, " ", Profile.last_name).ilike(pattern)) |
            (User.email.ilike(pattern))
        )
    total_items = int((await db.execute(count_stmt)).scalar_one())
    items = await _fetch_users_with_details(db, page, page_size, search=search, role="viewer", status=status)
    if page is None:
        page = 1
    if page_size is None:
        page_size = len(items)

    return build_paginated_response(items, page, page_size, total_items).model_dump()

async def export_users(page: int | None, page_size: int | None, db: AsyncSession) -> dict:
    if page is not None and page_size is not None:
        total_items = int(
            (
                await db.execute(
                    select(func.count(User.id))
                    .join(UserRole, UserRole.user_id == User.id)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(_admin_visible_users_clause(), Role.name == "user")
                )
            ).scalar_one()
        )
        items = await _fetch_users_with_details(db, page, page_size, role="user")
        return build_paginated_response(items, page, page_size, total_items).model_dump()
    else:
        # Default case: list all users
        items = await _fetch_users_with_details(db, role="user")
        total_items = len(items)
        return build_paginated_response(items, 1, max(total_items, 1), total_items).model_dump()

async def admin_create_user(
    payload: AdminUserCreateRequest,
    db: AsyncSession,
    background_tasks: BackgroundTasks | None = None,
    *,
    actor_user_id: UUID | None = None,
    actor_role: str | None = None,
) -> ApiResponse:
    from apps.accounts.services import assign_user_role
    from core.email_service import send_temporary_password_email

    email = payload.email.lower()
    role_name = payload.role.value if hasattr(payload.role, "value") else str(payload.role)
    if role_name not in ADMIN_MANAGED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="role must be one of: user, moderator, viewer",
        )

    from common.email_validation import validate_disposable_email
    from common.exceptions import ApiError
    from core.auth.config import settings as auth_settings

    try:
        validate_disposable_email(
            email,
            is_enabled=auth_settings.is_disposable_email_enabled,
        )
    except ApiError as exc:
        return ApiResponse(status=False, message=str(exc.message), data=None)

    existing_user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing_user:
        return ApiResponse(status=False, message="Email already registered", data=None)

    temporary_password = _generate_temporary_password()
    firebase_uid: str | None = None

    if role_name == "user":
        display_name = f"{payload.firstName} {payload.lastName}".strip()
        try:
            firebase_user = create_firebase_user(
                email=email,
                password=temporary_password,
                display_name=display_name or None,
            )
            firebase_uid = getattr(firebase_user, "uid", None)
            if not firebase_uid and isinstance(firebase_user, dict):
                firebase_uid = firebase_user.get("uid")
            if not firebase_uid:
                raise RuntimeError("Firebase user response did not include uid")
        except Exception as exc:
            logger.exception("Failed to create Firebase user for %s", email)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to create Firebase user",
            ) from exc

    user_status = UserStatus.active if role_name in ("moderator", "viewer") else UserStatus.pending

    now = datetime.now(timezone.utc)
    user = User(
        firebase_uid=firebase_uid,
        email=email,
        password_hash=PASSWORD_HASHER.hash(temporary_password),
        registration_type=RegistrationType.email,
        status=user_status,
        onboarding_status=OnboardingStatus.not_started,
        created_at=now,
        updated_at=now,
        email_verified_at=now,
    )

    try:
        db.add(user)
        await db.flush()

        await assign_user_role(db, user, role_name)

        profile = Profile(
            user_id=user.id,
            first_name=payload.firstName,
            last_name=payload.lastName,
            completeness_score=0,
            updated_at=now,
        )
        db.add(profile)
        await db.flush()

        from apps.profiles.services import calculate_completeness_score

        try:
            profile.completeness_score = await calculate_completeness_score(user.id, db)
            db.add(profile)
        except Exception:
            logger.exception("Failed to calculate profile completeness for admin-created user %s", user.id)

        await save_current_consent(
            db,
            user.id,
            source=CONSENT_SOURCE_ADMIN_CREATION,
        )

        from apps.administration.services.admin_activity_log_service import create_admin_activity_log

        await create_admin_activity_log(
            db,
            user_id=actor_user_id or user.id,
            role=actor_role,
            action="create",
            module="user",
            record_id=user.id,
            description=f"created {email}",
            metadata={
                "old": None,
                "new": {
                    "role": role_name,
                    "status": user_status.value if hasattr(user_status, "value") else str(user_status),
                },
            },
        )

        await db.commit()
    except Exception:
        await db.rollback()
        if firebase_uid:
            try:
                delete_firebase_user(firebase_uid)
            except Exception:
                logger.exception("Failed to roll back Firebase user %s after local create failure", firebase_uid)
        raise

    await db.refresh(user)
    await db.refresh(profile)

    stmt_user = select(User).options(selectinload(User.roles)).where(User.id == user.id)
    user = (await db.execute(stmt_user)).scalar_one()
    user_data = await build_user_base_response(user, profile, db)

    first_name = (payload.firstName or "").strip() or None
    email_sent = await send_temporary_password_email(
        email,
        temporary_password,
        first_name=first_name,
        role=role_name,
        background_tasks=background_tasks,
    )

    return ApiResponse(
        status=True,
        message="User created successfully",
        data={
            "user": user_data,
            "emailSent": email_sent,
            "authProvider": "firebase" if role_name == "user" else "local",
        },
    )

async def admin_get_user(user_id: str, db: AsyncSession) -> dict:
    from apps.moderation.repositories import get_latest
    from common.enums import ReportEntityType

    user_uuid = _coerce_uuid(user_id)
    user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == user_uuid))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    profile = (await db.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one_or_none()
    user_data = await build_user_base_response(user, profile, db)

    # Most recent status-change note (stored on moderation_history.comment).
    latest_history = await get_latest(
        db,
        entity_type=ReportEntityType.user,
        entity_id=user.id,
    )
    user_data["moderation_notes"] = latest_history.comment if latest_history else None
    return {"user": user_data}

async def _fetch_superadmin_user(db: AsyncSession) -> User | None:
    """Return the active Super Admin used as the fallback post moderator."""
    stmt = (
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.name == "superadmin", User.is_deleted.is_(False))
        .options(selectinload(User.roles))
        .order_by(User.created_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _reassign_moderator_posts_to_superadmin(
    db: AsyncSession,
    moderator_id: UUID,
    superadmin_id: UUID,
    *,
    now: datetime,
) -> None:
    """
    Reassign a departing moderator's review queue to Super Admin.

    Posts store the assignee on ``posts.moderator_id``. Comments and users are
    assigned through ``reports.moderator_id`` (entity_type post/comment/user).
    Transfer both before the moderator is soft-deleted.
    """
    from apps.feed.db_models import Post
    from apps.report.db_models import Report
    from sqlalchemy import update

    await db.execute(
        update(Post)
        .where(Post.moderator_id == moderator_id)
        .values(moderator_id=superadmin_id, updated_at=now)
    )
    await db.execute(
        update(Report)
        .where(Report.moderator_id == moderator_id)
        .values(moderator_id=superadmin_id, updated_at=now)
    )


def _soft_delete_user_record(
    user: User,
    *,
    now: datetime,
) -> None:
    """Apply the standard scheduled-deletion fields shared by all role types."""
    from apps.user_deletion.services.account_recovery_service import (
        apply_scheduled_deletion_fields,
    )

    apply_scheduled_deletion_fields(user, now=now)


async def _build_deleted_user_payload(
    user: User,
    db: AsyncSession,
) -> dict:
    """Build response payload after soft-delete fields are applied.

    Access side effects (token revoke / Stream deactivate) run via
    ``run_deletion_request_side_effects`` after commit. Content is left intact
    until permanent purge.
    """
    db.add(user)

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user.id))
    ).scalar_one_or_none()
    user_data = await build_user_base_response(user, profile, db)
    return {
        "deleted": True,
        "status": format_user_status(user.status),
        "deleted_at": user.deleted_at,
        "purge_after": user.purge_after,
        "user": user_data,
    }


async def admin_delete_users(
    user_ids: list[str],
    role: str,
    db: AsyncSession,
    *,
    actor_user_id: UUID | None = None,
    actor_role: str | None = None,
) -> dict:
    deleted_users = []
    deleted_user_objects: list[User] = []
    now = datetime.now(timezone.utc)

    superadmin: User | None = None
    if role == "moderator":
        superadmin = await _fetch_superadmin_user(db)
        if superadmin is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Super Admin not found",
            )

    for user_id in user_ids:
        try:
            user_uuid = _coerce_uuid(user_id)
        except Exception:  # nosec B112 -- intentional: skip malformed UUIDs
            continue

        user = (
            await db.execute(
                select(User)
                .options(selectinload(User.roles))
                .where(User.id == user_uuid, User.is_deleted.is_(False))
            )
        ).scalar_one_or_none()
        if user is None or user.role != role:
            continue

        if role == "moderator":
            # Transfer this moderator's assigned posts before soft-delete.
            await _reassign_moderator_posts_to_superadmin(
                db,
                user.id,
                superadmin.id,
                now=now,
            )

        _soft_delete_user_record(user, now=now)
        deleted_users.append(await _build_deleted_user_payload(user, db))
        deleted_user_objects.append(user)

        if actor_user_id is not None:
            from apps.administration.services.admin_activity_log_service import create_admin_activity_log

            await create_admin_activity_log(
                db,
                user_id=actor_user_id,
                role=actor_role,
                action="delete",
                module="user",
                record_id=user.id,
                description=f"deleted {user.email}",
                metadata={
                    "old": {
                        "status": role,
                        "is_deleted": False,
                    },
                    "new": {
                        "status": UserStatus.deleting.value,
                        "is_deleted": True,
                    },
                },
            )

    await db.commit()

    from apps.user_deletion.services.account_recovery_service import (
        run_deletion_request_side_effects,
    )

    for user in deleted_user_objects:
        await run_deletion_request_side_effects(user, db)

    return {"deleted_users": deleted_users}


async def admin_delete_user(user_id: str, role: str, db: AsyncSession) -> dict:
    result = await admin_delete_users([user_id], role, db)
    deleted_users = result["deleted_users"]
    if not deleted_users:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return deleted_users[0]

async def admin_edit_profile(
    user_id: str,
    payload: AdminEditProfileRequest,
    db: AsyncSession,
):
    from apps.profiles.db_models.profile_db_model import Profile
    from core.images import (
        file_exists,
        normalize_image_name,
        generate_download_url,
    )

    stmt = select(Profile).where(Profile.user_id == user_id)
    profile = (await db.execute(stmt)).scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found"
        )

    if payload.firstName is not None:
        profile.first_name = payload.firstName

    if payload.lastName is not None:
        profile.last_name = payload.lastName

    if payload.profile_photo_key is not None:
        if payload.profile_photo_key:
            if not file_exists(payload.profile_photo_key):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="profile_photo_key does not reference an uploaded file"
                )
            profile.profile_photo_url = normalize_image_name(payload.profile_photo_key)
        else:
            profile.profile_photo_url = None

    if payload.banner_photo_key is not None:
        if payload.banner_photo_key:
            if not file_exists(payload.banner_photo_key):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="banner_photo_key does not reference an uploaded file"
                )
            profile.banner_photo_url = normalize_image_name(payload.banner_photo_key)
        else:
            profile.banner_photo_url = None

    db.add(profile)

    stmt = (
        select(User)
        .options(selectinload(User.roles))
        .where(User.id == user_id)
    )
    user = (await db.execute(stmt)).scalar_one()

    await db.commit()
    await db.refresh(profile)

    user_data = await build_user_base_response(user, profile, db)
    return ApiResponse(
        status=True,
        message="Profile updated successfully",
        data={
            "user": user_data,
            "emailSent": False,
        },
    )

async def admin_update_user_status(
    user_id: str,
    new_status: AdminUserStatus,
    db: AsyncSession,
    *,
    moderator_id: UUID | None = None,
    comment: str | None = None,
    actor_role: str | None = None,
) -> dict:
    from apps.moderation.services import record_moderation_history
    from apps.notifications.services import notify_account_status
    from common.enums import ReportEntityType
    from core.auth.services import (
        disable_firebase_user,
        enable_firebase_user,
    )

    user_uuid = _coerce_uuid(user_id)
    target_status = UserStatus(new_status.value)

    user = (
        await db.execute(
            select(User)
            .options(selectinload(User.roles))
            .where(User.id == user_uuid)
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )

    already_same_status = user.status == target_status
    if already_same_status:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User is already {new_status.value}",
        )

    old_status = user.status.value if hasattr(user.status, "value") else str(user.status)
    user.status = target_status
    user.updated_at = datetime.now(timezone.utc)

    # 1) Audit trail (note lives on moderation_history.comment — not on users)
    await record_moderation_history(
        db,
        entity_type=ReportEntityType.user,
        entity_id=user.id,
        action=new_status.value,
        moderator_id=moderator_id,
        comment=comment,
    )
    status_actions = {
        AdminUserStatus.active.value: "activate",
        AdminUserStatus.suspended.value: "suspend",
        AdminUserStatus.banned.value: "ban",
    }
    status_verbs = {
        AdminUserStatus.active.value: "activated",
        AdminUserStatus.suspended.value: "suspended",
        AdminUserStatus.banned.value: "banned",
    }
    if moderator_id is not None:
        from apps.administration.services.admin_activity_log_service import (
            activity_person_label,
            create_admin_activity_log,
        )

        target_label = await activity_person_label(db, user.id) or (
            user.email if hasattr(user, "email") else "user"
        )
        await create_admin_activity_log(
            db,
            user_id=moderator_id,
            role=actor_role,
            action=status_actions.get(new_status.value, new_status.value),
            module="user",
            record_id=user.id,
            description=f"{status_verbs.get(new_status.value, new_status.value)} {target_label}",
            metadata={
                "old": {"status": old_status},
                "new": {"status": new_status.value},
            },
        )
    from apps.report.repositories.report_repository import clear_entity_report_queue_counts

    await clear_entity_report_queue_counts(
        db,
        entity_type=ReportEntityType.user,
        entity_id=user.id,
    )

    await db.commit()
    await db.refresh(user)

    # 2) Notify while the account can still receive push, then lock/unlock auth
    try:
        await notify_account_status(
            db,
            user_id=user.id,
            status=new_status.value,
            reason=comment,
            sender_user_id=moderator_id,
        )
    except Exception:
        logger.exception(
            "Failed to notify account status for user_id=%s status=%s",
            user.id,
            new_status.value,
        )

    # 3) Firebase enable/disable last so push delivery is not cut off early
    firebase_error = None
    if user.firebase_uid:
        try:
            if new_status == AdminUserStatus.active:
                enable_firebase_user(user.firebase_uid)
            else:
                disable_firebase_user(user.firebase_uid)
        except Exception as e:
            firebase_error = str(e)

    profile = (
        await db.execute(
            select(Profile)
            .where(Profile.user_id == user.id)
        )
    ).scalar_one_or_none()

    response = {
        "status": format_user_status(new_status),
        "already_exists": already_same_status,
        "user": await build_user_base_response(
            user,
            profile,
            db
        )
    }

    if firebase_error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Status updated locally but Firebase sync failed: {firebase_error}",
        )

    return response
