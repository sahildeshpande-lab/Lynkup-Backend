from __future__ import annotations


def get_post_recognition_milestones(
    previous_like_count: int,
    current_like_count: int,
    milestones: list[int],
) -> list[int]:
    """Return configured like milestones crossed by a like-count increase."""
    return [
        milestone
        for milestone in milestones
        if previous_like_count < milestone <= current_like_count
    ]


def select_post_recognition_milestone_for_notification(
    crossed_milestones: list[int],
) -> int | None:
    """Pick one milestone to notify for after a single like-count increase."""
    if not crossed_milestones:
        return None
    return max(crossed_milestones)

