"""Combined profile enrichment + user-state for /feed (Phase 7 STEP 4).

One SQL round trip with independent CTEs aggregated to JSON columns.
Never joins enrichment domains to engagement/reactions (no row multiplication).

Reuses ``map_feed_profile_enrichment_payload`` and ``map_feed_user_state_payload``
so Python DTO contracts stay identical to the separate loaders.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, INTEGER, UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from apps.engagement.repositories.engagement_repository import PostEngagementFlags
from apps.engagement.repositories.feed_user_state import (
    FeedUserState,
    _as_uuid,
    _coerce_json_list,
    _empty_reactions_group,
    map_feed_user_state_payload,
)
from apps.feed.services.profile_enrichment import (
    FeedProfileEnrichment,
    _collect_interest_ids,
    map_feed_profile_enrichment_payload,
)

_COMBINED_ENRICHMENT_USER_STATE_SQL = """
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
),
viewer_reactions AS (
    SELECT
        pr.post_id,
        pr.reaction_type::text AS reaction_type
    FROM post_reactions pr
    WHERE cardinality(CAST(:post_ids AS uuid[])) > 0
      AND pr.user_id = :viewer_user_id
      AND pr.post_id = ANY(CAST(:post_ids AS uuid[]))
),
viewer_bookmarks AS (
    SELECT b.post_id
    FROM bookmarks b
    WHERE cardinality(CAST(:post_ids AS uuid[])) > 0
      AND b.user_id = :viewer_user_id
      AND b.post_id = ANY(CAST(:post_ids AS uuid[]))
),
viewer_reposts AS (
    SELECT r.post_id
    FROM reposts r
    JOIN profiles p ON p.id = r.profile_id
    WHERE cardinality(CAST(:post_ids AS uuid[])) > 0
      AND p.user_id = :viewer_user_id
      AND r.post_id = ANY(CAST(:post_ids AS uuid[]))
      AND r.is_deleted = false
),
ranked_reactions AS (
    SELECT
        pr.post_id,
        pr.reaction_type::text AS reaction_type,
        pr.created_at,
        p.user_id AS profile_user_id,
        p.first_name,
        p.last_name,
        p.profile_photo_url,
        p.bio,
        u.name AS university_name,
        row_number() OVER (
            PARTITION BY pr.post_id, pr.reaction_type
            ORDER BY pr.created_at DESC
        ) AS rn
    FROM post_reactions pr
    LEFT JOIN profiles p ON p.user_id = pr.user_id
    LEFT JOIN universities u ON u.id = p.university_id
    WHERE cardinality(CAST(:post_ids AS uuid[])) > 0
      AND pr.post_id = ANY(CAST(:post_ids AS uuid[]))
),
latest_reactions AS (
    SELECT
        post_id,
        reaction_type,
        created_at,
        profile_user_id,
        first_name,
        last_name,
        profile_photo_url,
        bio,
        university_name
    FROM ranked_reactions
    WHERE rn <= :per_type_limit
),
moderation_rows AS (
    SELECT DISTINCT pr.post_id
    FROM post_revisions pr
    WHERE cardinality(CAST(:post_ids AS uuid[])) > 0
      AND pr.post_id = ANY(CAST(:post_ids AS uuid[]))
      AND pr.triggered_moderation_review = true
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
    ) AS connected_user_ids,
    COALESCE(
        (
            SELECT json_agg(post_id)
            FROM moderation_rows
        ),
        '[]'::json
    ) AS triggered_moderation_post_ids,
    COALESCE(
        (
            SELECT json_agg(
                json_build_object(
                    'post_id', post_id,
                    'reaction_type', reaction_type
                )
            )
            FROM viewer_reactions
        ),
        '[]'::json
    ) AS viewer_reactions,
    COALESCE(
        (
            SELECT json_agg(post_id)
            FROM viewer_bookmarks
        ),
        '[]'::json
    ) AS viewer_bookmarks,
    COALESCE(
        (
            SELECT json_agg(post_id)
            FROM viewer_reposts
        ),
        '[]'::json
    ) AS viewer_reposts,
    COALESCE(
        (
            SELECT json_agg(
                json_build_object(
                    'post_id', post_id,
                    'reaction_type', reaction_type,
                    'created_at', created_at,
                    'profile_user_id', profile_user_id,
                    'first_name', first_name,
                    'last_name', last_name,
                    'profile_photo_url', profile_photo_url,
                    'bio', bio,
                    'university_name', university_name
                )
                ORDER BY post_id, reaction_type, created_at DESC
            )
            FROM latest_reactions
        ),
        '[]'::json
    ) AS latest_reactions
"""


@dataclass(frozen=True)
class FeedEnrichmentAndUserState:
    enrichment: FeedProfileEnrichment
    user_state: FeedUserState
    triggered_moderation_post_ids: frozenset[UUID]


async def load_feed_enrichment_and_user_state(
    db: AsyncSession,
    current_user_id: UUID,
    profiles_by_user_id: dict[UUID, object],
    post_ids: list[UUID],
    *,
    per_type_limit: int = 3,
) -> FeedEnrichmentAndUserState:
    """Load enrichment + user-state in one SQL round trip."""
    ordered_ids = list(post_ids)
    unique_post_ids = sorted({pid for pid in ordered_ids if pid is not None}, key=str)

    if not profiles_by_user_id and not unique_post_ids:
        return FeedEnrichmentAndUserState(
            enrichment=FeedProfileEnrichment(
                profile_details={},
                requested_user_ids=set(),
                connected_user_ids=set(),
            ),
            user_state=FeedUserState(
                engagement=PostEngagementFlags.empty(),
                latest_reactions={},
            ),
            triggered_moderation_post_ids=frozenset(),
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

    stmt = text(_COMBINED_ENRICHMENT_USER_STATE_SQL).bindparams(
        bindparam("university_ids", type_=ARRAY(PGUUID(as_uuid=True))),
        bindparam("interest_ids", type_=ARRAY(INTEGER())),
        bindparam("target_user_ids", type_=ARRAY(PGUUID(as_uuid=True))),
        bindparam("post_ids", type_=ARRAY(PGUUID(as_uuid=True))),
        bindparam("viewer_user_id", type_=PGUUID(as_uuid=True)),
        bindparam("per_type_limit", type_=INTEGER()),
    )

    result = await db.execute(
        stmt,
        {
            "university_ids": university_ids,
            "interest_ids": interest_ids,
            "target_user_ids": target_user_ids,
            "post_ids": unique_post_ids,
            "viewer_user_id": current_user_id,
            "per_type_limit": per_type_limit,
        },
    )
    row = result.mappings().one()

    enrichment = map_feed_profile_enrichment_payload(
        profiles_by_user_id,
        universities=row["universities"],
        interests=row["interests"],
        requested_user_ids=row["requested_user_ids"],
        connected_user_ids=row["connected_user_ids"],
    )
    mapped_state = map_feed_user_state_payload(
        post_ids=ordered_ids,
        viewer_reactions=row["viewer_reactions"],
        viewer_bookmarks=row["viewer_bookmarks"],
        viewer_reposts=row["viewer_reposts"],
        latest_reactions=row["latest_reactions"],
    )
    unique_latest = {
        pid: mapped_state.latest_reactions.get(pid, _empty_reactions_group())
        for pid in dict.fromkeys(ordered_ids)
    }
    user_state = FeedUserState(
        engagement=mapped_state.engagement,
        latest_reactions=unique_latest,
    )
    triggered_moderation_post_ids = frozenset(
        _as_uuid(post_id)
        for post_id in _coerce_json_list(row["triggered_moderation_post_ids"])
        if post_id is not None
    )
    return FeedEnrichmentAndUserState(
        enrichment=enrichment,
        user_state=user_state,
        triggered_moderation_post_ids=triggered_moderation_post_ids,
    )
