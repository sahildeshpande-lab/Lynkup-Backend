from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.repositories import connection_repository
from apps.connections.services.connection_service import apply_relationship_flags
from apps.connections.services.recommendation_service import (
    _get_excluded_user_ids,
    build_recommendation_profile_payload,
)
from apps.profiles.db_models.profile_db_model import Profile
from core.images import generate_profile_image_url

TOP_MUTUAL_USERS = 3


def _name_sort_key(first_name: str | None, last_name: str | None, user_id: UUID) -> tuple:
    return ((first_name or "").lower(), (last_name or "").lower(), str(user_id))


def _mutual_profile_sort_key(profile: Profile) -> tuple:
    return _name_sort_key(profile.first_name, profile.last_name, profile.user_id)


def _candidate_sort_key(item: dict) -> tuple:
    return (
        -item["mutual_connections"]["count"],
        (item.get("first_name") or "").lower(),
        (item.get("last_name") or "").lower(),
        str(item["user_id"]),
    )


def _mutual_user_payload(profile: Profile) -> dict:
    return {
        "user_id": profile.user_id,
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "profilePhoto_url": (
            generate_profile_image_url(profile.profile_photo_url)
            if profile.profile_photo_url
            else None
        ),
    }


async def get_mutual_recommendations(db: AsyncSession, user_id: UUID) -> list[dict]:
    """Recommend people who share at least one mutual connection with the viewer."""
    _friend_ids, candidate_mutuals = await connection_repository.fetch_second_hop_mutuals(
        db, user_id
    )
    if not candidate_mutuals:
        return []

    excluded_ids = await _get_excluded_user_ids(db, user_id)
    excluded_ids.add(user_id)

    candidate_ids = [
        candidate_id
        for candidate_id, mutuals in candidate_mutuals.items()
        if candidate_id not in excluded_ids and mutuals
    ]
    if not candidate_ids:
        return []

    eligible_rows = await connection_repository.fetch_eligible_recommendation_profiles(
        db, user_id, candidate_ids
    )
    eligible_by_id: dict[UUID, tuple] = {}
    for row in eligible_rows:
        eligible_by_id[row[0].user_id] = row
    if not eligible_by_id:
        return []

    mutual_ids_needed: set[UUID] = set()
    for candidate_id in eligible_by_id:
        mutual_ids_needed.update(candidate_mutuals[candidate_id])

    mutual_profiles = await connection_repository.fetch_visible_profiles_by_user_ids(
        db, list(mutual_ids_needed)
    )

    items: list[dict] = []
    for candidate_id, (profile, uni_id, university_name, university_website) in eligible_by_id.items():
        overlapping_ids = candidate_mutuals[candidate_id]
        visible_mutuals = [
            mutual_profiles[mutual_id]
            for mutual_id in overlapping_ids
            if mutual_id in mutual_profiles
        ]
        visible_mutuals.sort(key=_mutual_profile_sort_key)
        candidate_data = build_recommendation_profile_payload(
            profile, uni_id, university_name, university_website
        )
        candidate_data["mutual_connections"] = {
            "count": len(overlapping_ids),
            "users": [_mutual_user_payload(mutual) for mutual in visible_mutuals[:TOP_MUTUAL_USERS]],
        }
        items.append(candidate_data)

    items.sort(key=_candidate_sort_key)

    from apps.connections.services import get_relationship_flags

    flags_map = await get_relationship_flags(db, user_id, [item["user_id"] for item in items])
    for item in items:
        apply_relationship_flags(item, flags_map, item["user_id"])

    return items
