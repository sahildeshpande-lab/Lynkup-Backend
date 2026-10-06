from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from apps.accounts.db_models import User
from apps.administration.db_models.admin_activity_log_db_model import AdminActivityLog
from apps.administration.repositories import (
    create_admin_activity_log_record,
    list_admin_activity_logs as fetch_admin_activity_logs,
)
from apps.administration.repositories.admin_activity_log_repository import (
    _staff_display_name,
)
from apps.profiles.db_models import Profile
from apps.profiles.db_models.country_db_model import Country
from common.enums import AdminActivityLogOrder, AdminActivityLogRole, AdminActivityLogSort
from common.pagination import build_paginated_response

ADMIN_ACTIVITY_LOG_ROLES = frozenset({"superadmin", "moderator"})
SUPERADMIN_ACTOR_LABEL = "Super Admin"
MODERATOR_ACTOR_FALLBACK = "Moderator"
POST_MODERATION_ACTIVITY_ACTIONS = frozenset(
    {"published", "flagged", "rejected", "reinstate"}
)


def normalize_admin_role(role: object) -> str:
    if role is None:
        return ""
    value = role.value if hasattr(role, "value") else role
    return str(value).strip().lower()


async def activity_person_label(
    db: AsyncSession,
    user_id: UUID | None,
) -> str | None:
    """Return first+last name, falling back to email."""
    if user_id is None:
        return None
    row = (
        await db.execute(
            select(Profile.first_name, Profile.last_name, User.email)
            .select_from(User)
            .outerjoin(Profile, Profile.user_id == User.id)
            .where(User.id == user_id)
        )
    ).first()
    if row is None:
        return None
    return _staff_display_name(row[0], row[1], row[2])


async def activity_actor_label(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    role: object,
) -> str:
    """Superadmin is 'Super Admin' or 'Super Admin {first_name}'; moderators use profile name."""
    if normalize_admin_role(role) == "superadmin":
        first_name = await _activity_first_name(db, user_id)
        if first_name:
            return f"{SUPERADMIN_ACTOR_LABEL} {first_name}"
        return SUPERADMIN_ACTOR_LABEL
    name = await activity_person_label(db, user_id)
    return name or MODERATOR_ACTOR_FALLBACK


async def _activity_first_name(
    db: AsyncSession,
    user_id: UUID | None,
) -> str | None:
    if user_id is None:
        return None
    row = (
        await db.execute(
            select(Profile.first_name).where(Profile.user_id == user_id)
        )
    ).first()
    if row is None:
        return None
    value = row[0] if not isinstance(row, str) else row
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def format_email_template_activity_label(template_name: str) -> str:
    """Human label for template activity logs, e.g. otp_email -> Email Otp."""
    known = {
        "otp_email": "Email Otp",
        "reset_password_email": "Reset Password Email",
        "graduation_email": "Graduation Email",
        "graduation_completion_email": "Graduation Email",
        "data_export_ready_email": "Data Export Ready Email",
        "account_created_email": "Account Created Email",
        "password_changed_email": "Password Changed Email",
        "email_verified_email": "Email Verified Email",
        "temporary_password_email": "Temporary Password Email",
        "profile_updated_email": "Profile Updated Email",
        "lynkup_response_email": "Lynkup Response Email",
        "connection_reminder_email": "Connection Reminder Email",
        "post_review_email": "Post Review Email",
        "bulk_campaign_email": "Bulk Campaign Email",
        "notification_send_email": "Notification Send Email",
        "notification_topic_email": "Notification Topic Email",
        "notification_broadcast_email": "Notification Broadcast Email",
        "notification_configuration_email": "Notification Configuration Email",
        "notification_resend_otp_email": "Notification Resend Otp Email",
        "notification_user_creation_email": "Notification User Creation Email",
    }
    if template_name in known:
        return known[template_name]
    base = template_name.removesuffix("_email").replace("_", " ").strip()
    if not base:
        return template_name
    return f"{base.title()} Email"


def format_email_template_activity_description(action: str, template_name: str) -> str:
    """Build description body; create_admin_activity_log prefixes the actor.

    Example: ``updated the Email Otp otp_email`` →
    ``Super Admin Ada updated the Email Otp otp_email``.
    """
    label = format_email_template_activity_label(template_name)
    return f"{action} the {label} {template_name}"


def compose_person_label(
    first_name: str | None,
    last_name: str | None,
    email: str | None = None,
) -> str | None:
    return _staff_display_name(first_name, last_name, email)


def format_post_moderation_description(action: str, author_label: str | None) -> str:
    """Build activity-log copy for post flag/publish/reinstate/reject.

    ``create_admin_activity_log`` prefixes the actor, producing e.g.
    ``Super Admin published the Jane Doe post`` or
    ``Ada Moderator flagged the Jane Doe post``.
    """
    if action in POST_MODERATION_ACTIVITY_ACTIONS and author_label:
        return f"{action} the {author_label} post"
    return f"{action} a post"


def format_field_changes(
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
) -> str:
    previous = old or {}
    current = new or {}
    parts: list[str] = []
    for key in current:
        if previous.get(key) != current.get(key):
            if key == "learning_spotlight_papers_count":
                parts.append(f"learning spotlight paper count to {json_safe(current[key])}")
            elif key == "is_enabled":
                action_word = "enabled" if current[key] else "disabled"
                parts.append(f"{action_word} the learning spotlight feature")
            elif key == "is_pushnotification_enabled":
                action_word = "enabled" if current[key] else "disabled"
                parts.append(f"{action_word} learning spotlight push notifications")
            else:
                parts.append(f"{key} {json_safe(current[key])}")
    return ", ".join(parts)


def json_safe(value: Any) -> Any:
    """Convert values to JSON-serializable forms without storing secrets."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def country_details_payload(
    country_id: object | None,
    country_name: str | None = None,
) -> dict[str, Any]:
    cid = json_safe(country_id) if country_id else None
    return {"id": cid, "country_name": country_name}


def _country_id_from_snapshot(block: dict[str, Any]) -> str | None:
    raw = block.get("country_id")
    if raw is None:
        details = block.get("country_details")
        if isinstance(details, dict):
            raw = details.get("id")
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def collect_country_ids_from_metadata(metadata: Any) -> set[str]:
    ids: set[str] = set()
    if not isinstance(metadata, dict):
        return ids
    for side in ("old", "new"):
        block = metadata.get(side)
        if not isinstance(block, dict):
            continue
        country_id = _country_id_from_snapshot(block)
        if country_id:
            ids.add(country_id)
    return ids


def attach_country_details(
    metadata: Any,
    country_names: dict[str, str | None],
) -> Any:
    if not isinstance(metadata, dict):
        return metadata
    enriched = dict(metadata)
    for side in ("old", "new"):
        block = enriched.get(side)
        if not isinstance(block, dict):
            continue
        snapshot = dict(block)
        country_id = _country_id_from_snapshot(snapshot)
        existing = snapshot.get("country_details")
        if country_id is None and not isinstance(existing, dict):
            continue
        existing_name = None
        if isinstance(existing, dict):
            raw_name = existing.get("country_name")
            if isinstance(raw_name, str) and raw_name.strip():
                existing_name = raw_name
        snapshot["country_details"] = country_details_payload(
            country_id,
            existing_name if existing_name is not None else country_names.get(country_id),
        )
        enriched[side] = snapshot
    return enriched


async def load_country_names(
    db: AsyncSession,
    country_ids: set[str],
) -> dict[str, str | None]:
    uuids: list[UUID] = []
    for raw in country_ids:
        try:
            uuids.append(UUID(str(raw)))
        except (TypeError, ValueError, AttributeError):
            continue
    if not uuids:
        return {}
    rows = (
        await db.execute(select(Country.id, Country.name).where(Country.id.in_(uuids)))
    ).all()
    return {str(country_id): name for country_id, name in rows}


async def country_names_for_activity_logs(
    db: AsyncSession,
    rows: list[tuple[AdminActivityLog, str | None]],
) -> dict[str, str | None]:
    ids: set[str] = set()
    for row, _user_name in rows:
        ids |= collect_country_ids_from_metadata(row.log_metadata)
    if not ids:
        return {}
    return await load_country_names(db, ids)


def serialize_admin_activity_log(
    row: AdminActivityLog,
    *,
    user_name: str | None = None,
    country_names: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    metadata = row.log_metadata
    if country_names is not None:
        metadata = attach_country_details(metadata, country_names)
    return {
        "id": row.id,
        "user_id": row.user_id,
        "user_name": user_name,
        "role": row.role,
        "action": row.action,
        "module": row.module,
        "record_id": row.record_id,
        "description": row.description,
        "metadata": metadata,
        "is_read": row.is_read,
        "read_at": row.read_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def create_admin_activity_log(
    db: AsyncSession,
    *,
    user_id: UUID,
    role: object,
    action: str,
    module: str,
    record_id: UUID | None = None,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> AdminActivityLog | None:
    """Persist one admin activity log after a successful staff action.

    Only ``superadmin`` and ``moderator`` roles create a row. Viewer, user,
    system, and cron actors are ignored. Does not commit unless ``commit`` is
    True so the caller can keep the log in the same business transaction.

    ``description`` is prefixed with the actor label, e.g. ``created a@b.com``
    becomes ``Super Admin created a@b.com``.
    """
    role_value = normalize_admin_role(role)
    if role_value not in ADMIN_ACTIVITY_LOG_ROLES:
        return None

    stored_description = description
    if description:
        actor = await activity_actor_label(db, user_id=user_id, role=role_value)
        stored_description = f"{actor} {description}".strip()

    record = await create_admin_activity_log_record(
        db,
        user_id=user_id,
        role=role_value,
        action=action,
        module=module,
        record_id=record_id,
        description=stored_description,
        metadata=json_safe(metadata) if metadata is not None else None,
    )
    if commit:
        await db.commit()
    return record


async def list_admin_activity_logs_service(
    db: AsyncSession,
    *,
    module: str | None = None,
    role: AdminActivityLogRole | str | None = None,
    moderator_id: UUID | None = None,
    search: str | None = None,
    sort: AdminActivityLogSort | None = None,
    order: AdminActivityLogOrder | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    paginate = page is not None and page_size is not None
    offset = ((page or 1) - 1) * (page_size or 0) if paginate else 0
    limit = page_size if paginate else None
    rows, total_items = await fetch_admin_activity_logs(
        db,
        module=module,
        role=role,
        moderator_id=moderator_id,
        search=search,
        sort=sort or AdminActivityLogSort.created_at,
        order=order or AdminActivityLogOrder.desc,
        offset=offset,
        limit=limit,
    )
    country_names = await country_names_for_activity_logs(db, rows)
    items = [
        serialize_admin_activity_log(
            row,
            user_name=user_name,
            country_names=country_names,
        )
        for row, user_name in rows
    ]
    if paginate:
        return build_paginated_response(
            items,
            page or 1,
            page_size or 1,
            total_items,
        ).model_dump()
    return {"items": items}


async def list_admin_notifications_service(
    db: AsyncSession,
    *,
    current_user: User,
    moderator_id: UUID | None = None,
    is_read: bool | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    from apps.administration.repositories.admin_activity_log_repository import (
        count_unread_admin_notification_activity_logs,
        list_admin_notification_activity_logs,
    )

    target_moderator_id = moderator_id
    if target_moderator_id is None and current_user.role == "moderator":
        target_moderator_id = current_user.id

    created_after = current_user.created_at
    if target_moderator_id is not None and target_moderator_id != current_user.id:
        target_created_at = (
            await db.execute(select(User.created_at).where(User.id == target_moderator_id))
        ).scalar_one_or_none()
        if target_created_at is not None:
            created_after = target_created_at

    paginate = page is not None or page_size is not None
    resolved_page = page if page is not None else 1
    resolved_page_size = page_size if page_size is not None else 20
    offset = (resolved_page - 1) * resolved_page_size if paginate else 0
    limit = resolved_page_size if paginate else None

    rows, total_items = await list_admin_notification_activity_logs(
        db,
        moderator_id=target_moderator_id,
        is_read=is_read,
        created_after=created_after,
        offset=offset,
        limit=limit,
    )
    country_names = await country_names_for_activity_logs(db, rows)
    items = [
        serialize_admin_activity_log(
            row,
            user_name=user_name,
            country_names=country_names,
        )
        for row, user_name in rows
    ]

    total_unread = await count_unread_admin_notification_activity_logs(
        db,
        moderator_id=target_moderator_id,
        created_after=created_after,
    )

    if paginate:
        paginated_dict = build_paginated_response(
            items,
            resolved_page,
            resolved_page_size,
            total_items,
        ).model_dump(mode="json")
        return {
            "items": paginated_dict["items"],
            "Totalcount": total_unread,
            "page": paginated_dict["page"],
            "pageSize": paginated_dict["pageSize"],
            "totalItems": paginated_dict["totalItems"],
            "totalPages": paginated_dict["totalPages"],
        }
    return {
        "items": items,
        "Totalcount": total_unread,
    }


async def mark_admin_notification_as_read(
    db: AsyncSession,
    *,
    notification_id: UUID,
) -> dict[str, Any] | None:
    from apps.administration.repositories.admin_activity_log_repository import (
        get_admin_activity_log_by_id,
        mark_admin_activity_log_read,
    )

    found = await get_admin_activity_log_by_id(db, notification_id)
    if found is None:
        return None
    log, user_name = found
    if not log.is_read:
        log = await mark_admin_activity_log_read(db, log)
        await db.commit()
    country_names = await country_names_for_activity_logs(db, [(log, user_name)])
    return serialize_admin_activity_log(
        log,
        user_name=user_name,
        country_names=country_names,
    )


async def mark_all_admin_notifications_as_read(
    db: AsyncSession,
    *,
    current_user: User,
    moderator_id: UUID | None = None,
) -> int:
    from apps.administration.repositories.admin_activity_log_repository import (
        mark_all_admin_activity_logs_read,
    )

    target_moderator_id = moderator_id
    if target_moderator_id is None and current_user.role == "moderator":
        target_moderator_id = current_user.id

    created_after = current_user.created_at
    if target_moderator_id is not None and target_moderator_id != current_user.id:
        target_created_at = (
            await db.execute(select(User.created_at).where(User.id == target_moderator_id))
        ).scalar_one_or_none()
        if target_created_at is not None:
            created_after = target_created_at

    updated_count = await mark_all_admin_activity_logs_read(
        db,
        moderator_id=target_moderator_id,
        created_after=created_after,
    )
    await db.commit()
    return updated_count

