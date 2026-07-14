from __future__ import annotations

from apps.engagement.schemas import EngagementAuthor
from apps.profiles.db_models import Profile
from apps.profiles.db_models.university_db_model import University
from core.images import generate_profile_image_url


def format_engagement_author(
    profile: Profile | None,
    university: University | None,
) -> EngagementAuthor:
    return EngagementAuthor(
        profile_id=profile.id if profile else None,
        first_name=profile.first_name if profile else None,
        last_name=profile.last_name if profile else None,
        university=university.name if university else None,
        profilePhoto_url=(
            generate_profile_image_url(profile.profile_photo_url)
            if profile and profile.profile_photo_url
            else None
        ),
        major=profile.major if profile else None,
        minor=profile.minor if profile else None,
        edu_level=profile.edu_level if profile else None,
        bio=profile.bio if profile else None,
    )
