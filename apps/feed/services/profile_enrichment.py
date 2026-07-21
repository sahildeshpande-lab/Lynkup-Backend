from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.db_models import ConnectionRequest
from apps.profiles.db_models import AcademicInterest, University


async def load_profile_details(
    db: AsyncSession,
    profiles_by_user_id: dict[UUID, object],
) -> dict[UUID, dict]:
    """Batch-resolve profile fields that are stored as foreign-key IDs."""
    university_ids = {
        profile.university_id
        for profile in profiles_by_user_id.values()
        if getattr(profile, "university_id", None) is not None
    }
    profile_interest_ids: dict[UUID, list[int]] = {}
    for user_id, profile in profiles_by_user_id.items():
        normalized_ids = []
        for interest_id in (
            getattr(profile, "profile_interests_id", None) or []
        ):
            try:
                normalized_ids.append(int(interest_id))
            except (TypeError, ValueError):
                continue
        profile_interest_ids[user_id] = normalized_ids
    interest_ids = {
        interest_id
        for normalized_ids in profile_interest_ids.values()
        for interest_id in normalized_ids
    }

    university_names: dict[UUID, str] = {}
    if university_ids:
        rows = (
            await db.execute(
                select(University.id, University.name).where(
                    University.id.in_(university_ids)
                )
            )
        ).all()
        university_names = {row.id: row.name for row in rows}

    interest_names: dict[int, str] = {}
    if interest_ids:
        rows = (
            await db.execute(
                select(AcademicInterest.id, AcademicInterest.name).where(
                    AcademicInterest.id.in_(interest_ids)
                )
            )
        ).all()
        interest_names = {row.id: row.name for row in rows}

    return {
        user_id: {
            "university": university_names.get(
                getattr(profile, "university_id", None)
            ),
            "bio": getattr(profile, "bio", None),
            "academic_interest": [
                interest_names[interest_id]
                for interest_id in profile_interest_ids[user_id]
                if interest_id in interest_names
            ],
            "major": getattr(profile, "major", None),
            "minor": getattr(profile, "minor", None),
        }
        for user_id, profile in profiles_by_user_id.items()
    }


async def load_requested_user_ids(
    db: AsyncSession,
    current_user_id: UUID,
    target_user_ids: set[UUID],
) -> set[UUID]:
    """Return users with a pending request in either direction with the viewer."""
    if not target_user_ids:
        return set()

    rows = (
        await db.execute(
            select(ConnectionRequest).where(
                ConnectionRequest.status == "pending",
                or_(
                    and_(
                        ConnectionRequest.sender_user_id == current_user_id,
                        ConnectionRequest.receiver_user_id.in_(target_user_ids),
                    ),
                    and_(
                        ConnectionRequest.receiver_user_id == current_user_id,
                        ConnectionRequest.sender_user_id.in_(target_user_ids),
                    ),
                ),
            )
        )
    ).scalars().all()

    requested_ids: set[UUID] = set()
    for request in rows:
        if request.sender_user_id == current_user_id:
            requested_ids.add(request.receiver_user_id)
        else:
            requested_ids.add(request.sender_user_id)
    return requested_ids
