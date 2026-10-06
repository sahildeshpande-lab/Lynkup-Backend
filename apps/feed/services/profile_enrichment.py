"""Feed profile enrichment (Phase 4 + Phase 7 connections).

Resolves university names, academic-interest names, pending connection
request targets, and active connection peers for feed authors/reposters.

``load_profile_details`` / ``load_requested_user_ids`` remain for non-feed
callers. Feed uses ``load_feed_profile_enrichment`` (one SQL round trip).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, bindparam, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, INTEGER, UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.db_models import ConnectionRequest
from apps.profiles.db_models import AcademicInterest, University

# Independent CTEs aggregated to JSON — never join universities ⨯ interests ⨯
# requests ⨯ connections (avoids row multiplication).
_FEED_PROFILE_ENRICHMENT_SQL = """
WITH university_rows AS (
    SELECT u.id, u.name, u.website
    FROM universities u
    WHERE cardinality(CAST(:university_ids AS uuid[])) > 0
      AND u.id = ANY(CAST(:university_ids AS uuid[]))
),
interest_rows AS (
    SELECT ai.id, ai.name
    FROM academic_interests ai
    WHERE cardinality(CAST(:interest_ids AS int[])) > 0
      AND ai.id = ANY(CAST(:interest_ids AS int[]))
),
requested_rows AS (
    SELECT
        CASE
            WHEN cr.sender_user_id = :viewer_user_id THEN cr.receiver_user_id
            ELSE cr.sender_user_id
        END AS other_user_id
    FROM connection_requests cr
    WHERE cardinality(CAST(:target_user_ids AS uuid[])) > 0
      AND cr.status = 'pending'
      AND (
            (
                cr.sender_user_id = :viewer_user_id
                AND cr.receiver_user_id = ANY(CAST(:target_user_ids AS uuid[]))
            )
            OR (
                cr.receiver_user_id = :viewer_user_id
                AND cr.sender_user_id = ANY(CAST(:target_user_ids AS uuid[]))
            )
      )
),
connection_rows AS (
    SELECT
        CASE
            WHEN c.user_low_id = :viewer_user_id THEN c.user_high_id
            ELSE c.user_low_id
        END AS other_user_id
    FROM connections c
    WHERE c.is_active = true
      AND cardinality(CAST(:target_user_ids AS uuid[])) > 0
      AND (
            (c.user_low_id = :viewer_user_id
             AND c.user_high_id = ANY(CAST(:target_user_ids AS uuid[])))
            OR (c.user_high_id = :viewer_user_id
                AND c.user_low_id = ANY(CAST(:target_user_ids AS uuid[])))
      )
)
SELECT
    COALESCE(
        (
            SELECT json_agg(
                json_build_object(
                    'id', id,
                    'name', name,
                    'website', website
                )
            )
            FROM university_rows
        ),
        '[]'::json
    ) AS universities,
    COALESCE(
        (
            SELECT json_agg(
                json_build_object(
                    'id', id,
                    'name', name
                )
            )
            FROM interest_rows
        ),
        '[]'::json
    ) AS interests,
    COALESCE(
        (
            SELECT json_agg(other_user_id)
            FROM requested_rows
        ),
        '[]'::json
    ) AS requested_user_ids,
    COALESCE(
        (
            SELECT json_agg(other_user_id)
            FROM connection_rows
        ),
        '[]'::json
    ) AS connected_user_ids
"""


@dataclass(frozen=True)
class FeedProfileEnrichment:
    profile_details: dict[UUID, dict]
    requested_user_ids: set[UUID]
    connected_user_ids: set[UUID]


def _coerce_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    if isinstance(value, list):
        return value
    return list(value)


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _collect_interest_ids(
    profiles_by_user_id: dict[UUID, object],
) -> dict[UUID, list[int]]:
    profile_interest_ids: dict[UUID, list[int]] = {}
    for user_id, profile in profiles_by_user_id.items():
        normalized_ids: list[int] = []
        for interest_id in getattr(profile, "profile_interests_id", None) or []:
            try:
                normalized_ids.append(int(interest_id))
            except (TypeError, ValueError):
                continue
        profile_interest_ids[user_id] = normalized_ids
    return profile_interest_ids


def _build_profile_details(
    profiles_by_user_id: dict[UUID, object],
    *,
    university_map: dict[UUID, tuple[str | None, str | None]],
    interest_names: dict[int, str],
    profile_interest_ids: dict[UUID, list[int]],
) -> dict[UUID, dict]:
    result: dict[UUID, dict] = {}
    for user_id, profile in profiles_by_user_id.items():
        uni_id = getattr(profile, "university_id", None)
        uni_name, uni_website = (
            university_map.get(uni_id, (None, None)) if uni_id else (None, None)
        )
        result[user_id] = {
            "university": uni_name,
            "university_details": {
                "id": str(uni_id) if uni_id else None,
                "university_name": uni_name,
                "university_website": uni_website,
            },
            "bio": getattr(profile, "bio", None),
            "academic_interest": [
                interest_names[interest_id]
                for interest_id in profile_interest_ids.get(user_id, [])
                if interest_id in interest_names
            ],
            "major": getattr(profile, "major", None),
            "minor": getattr(profile, "minor", None),
            "education_level": getattr(profile, "edu_level", None),
        }
    return result


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
    profile_interest_ids = _collect_interest_ids(profiles_by_user_id)
    interest_ids = {
        interest_id
        for normalized_ids in profile_interest_ids.values()
        for interest_id in normalized_ids
    }

    university_map: dict[UUID, tuple[str | None, str | None]] = {}
    if university_ids:
        rows = (
            await db.execute(
                select(University.id, University.name, University.website).where(
                    University.id.in_(university_ids)
                )
            )
        ).all()
        university_map = {
            row.id: (row.name, getattr(row, "website", None))
            for row in rows
        }

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

    return _build_profile_details(
        profiles_by_user_id,
        university_map=university_map,
        interest_names=interest_names,
        profile_interest_ids=profile_interest_ids,
    )


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


def map_feed_profile_enrichment_payload(
    profiles_by_user_id: dict[UUID, object],
    *,
    universities: Any,
    interests: Any,
    requested_user_ids: Any,
    connected_user_ids: Any = None,
) -> FeedProfileEnrichment:
    """Map raw JSON enrichment columns into profile_details + request/connection sets."""
    profile_interest_ids = _collect_interest_ids(profiles_by_user_id)

    university_map: dict[UUID, tuple[str | None, str | None]] = {}
    for row in _coerce_json_list(universities):
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        university_map[_as_uuid(row["id"])] = (row.get("name"), row.get("website"))

    interest_names: dict[int, str] = {}
    for row in _coerce_json_list(interests):
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        try:
            interest_names[int(row["id"])] = row.get("name")
        except (TypeError, ValueError):
            continue

    requested: set[UUID] = set()
    for item in _coerce_json_list(requested_user_ids):
        if item is None:
            continue
        requested.add(_as_uuid(item if not isinstance(item, dict) else item.get("other_user_id")))

    connected: set[UUID] = set()
    for item in _coerce_json_list(connected_user_ids):
        if item is None:
            continue
        connected.add(_as_uuid(item if not isinstance(item, dict) else item.get("other_user_id")))

    details = _build_profile_details(
        profiles_by_user_id,
        university_map=university_map,
        interest_names=interest_names,
        profile_interest_ids=profile_interest_ids,
    )
    return FeedProfileEnrichment(
        profile_details=details,
        requested_user_ids=requested,
        connected_user_ids=connected,
    )


async def load_feed_profile_enrichment(
    db: AsyncSession,
    current_user_id: UUID,
    profiles_by_user_id: dict[UUID, object],
) -> FeedProfileEnrichment:
    """Load university/interest names, pending requests, and connections in one SQL trip.

    Bio/major/minor/education_level come from already-hydrated profile objects
    (no extra SQL). Empty profile maps skip the database entirely.
    """
    if not profiles_by_user_id:
        return FeedProfileEnrichment(
            profile_details={},
            requested_user_ids=set(),
            connected_user_ids=set(),
        )

    university_ids = sorted(
        {
            profile.university_id
            for profile in profiles_by_user_id.values()
            if getattr(profile, "university_id", None) is not None
        },
        key=str,
    )
    profile_interest_ids = _collect_interest_ids(profiles_by_user_id)
    interest_ids = sorted(
        {
            interest_id
            for normalized_ids in profile_interest_ids.values()
            for interest_id in normalized_ids
        }
    )
    target_user_ids = sorted(profiles_by_user_id.keys(), key=str)

    stmt = text(_FEED_PROFILE_ENRICHMENT_SQL).bindparams(
        bindparam("university_ids", type_=ARRAY(PGUUID(as_uuid=True))),
        bindparam("interest_ids", type_=ARRAY(INTEGER())),
        bindparam("target_user_ids", type_=ARRAY(PGUUID(as_uuid=True))),
        bindparam("viewer_user_id", type_=PGUUID(as_uuid=True)),
    )
    result = await db.execute(
        stmt,
        {
            "university_ids": university_ids,
            "interest_ids": interest_ids,
            "target_user_ids": target_user_ids,
            "viewer_user_id": current_user_id,
        },
    )
    row = result.mappings().one()
    return map_feed_profile_enrichment_payload(
        profiles_by_user_id,
        universities=row["universities"],
        interests=row["interests"],
        requested_user_ids=row["requested_user_ids"],
        connected_user_ids=row["connected_user_ids"],
    )
