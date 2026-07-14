from __future__ import annotations

from uuid import UUID

from common.enums import ProfileVisibility


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped.lower() if stripped else None


def compute_relevance_score(
    *,
    viewer_major: str | None,
    viewer_minor: str | None,
    viewer_university_id: UUID | None,
    author_major: str | None,
    author_minor: str | None,
    author_university_id: UUID | None,
) -> int:
    score = 0
    if _normalize(viewer_major) and _normalize(viewer_major) == _normalize(author_major):
        score += 1
    if _normalize(viewer_minor) and _normalize(viewer_minor) == _normalize(author_minor):
        score += 1
    if viewer_university_id and author_university_id and viewer_university_id == author_university_id:
        score += 1
    return score


def viewer_has_relevance_criteria(
    *,
    viewer_major: str | None,
    viewer_minor: str | None,
    viewer_university_id: UUID | None,
) -> bool:
    return bool(_normalize(viewer_major) or _normalize(viewer_minor) or viewer_university_id)


def has_relevance_match(
    *,
    viewer_major: str | None,
    viewer_minor: str | None,
    viewer_university_id: UUID | None,
    author_major: str | None,
    author_minor: str | None,
    author_university_id: UUID | None,
) -> bool:
    return compute_relevance_score(
        viewer_major=viewer_major,
        viewer_minor=viewer_minor,
        viewer_university_id=viewer_university_id,
        author_major=author_major,
        author_minor=author_minor,
        author_university_id=author_university_id,
    ) >= 1


def is_feed_visible(
    *,
    profile_visibility: ProfileVisibility | str,
    is_connected: bool,
    relevance_score: int = 0,
    viewer_has_relevance_criteria: bool = True,
) -> bool:
    visibility = (
        profile_visibility.value
        if isinstance(profile_visibility, ProfileVisibility)
        else str(profile_visibility)
    )
    if visibility in {ProfileVisibility.private.value, ProfileVisibility.connections_only.value}:
        return is_connected
    if visibility == ProfileVisibility.public.value:
        return True
    return False
