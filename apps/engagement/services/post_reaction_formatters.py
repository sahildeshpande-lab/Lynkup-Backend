from __future__ import annotations

from apps.engagement.db_models import PostReaction
from apps.engagement.schemas import PostReactionsGrouped, PostReactorProfile
from apps.engagement.services.author_service import format_engagement_author
from apps.engagement.services.reaction_service import format_user_reaction
from common.enums import ReactionType


def format_post_reactor_profile(
    reaction: PostReaction,
    profile,
    university,
) -> PostReactorProfile:
    author = format_engagement_author(profile, university)
    return PostReactorProfile(
        profile_id=author.profile_id,
        first_name=author.first_name,
        last_name=author.last_name,
        profilePhoto_url=author.profilePhoto_url,
        bio=author.bio,
        reaction_type=format_user_reaction(reaction.reaction_type),
        reacted_at=reaction.created_at,
    )


def empty_post_reactions_group() -> PostReactionsGrouped:
    return PostReactionsGrouped()


def group_post_reactor_profiles(reactors: list[PostReactorProfile]) -> PostReactionsGrouped:
    grouped = empty_post_reactions_group()
    for reactor in reactors:
        bucket = getattr(grouped, reactor.reaction_type, None)
        if bucket is not None:
            bucket.append(reactor)
    return grouped


def build_post_reactions_from_rows(
    rows: list[tuple[PostReaction, object, object]],
) -> PostReactionsGrouped:
    reactors = [
        format_post_reactor_profile(reaction, profile, university)
        for reaction, profile, university in rows
    ]
    return group_post_reactor_profiles(reactors)


def build_post_reactions_response(
    reactors: list[PostReactorProfile],
    *,
    reaction_type: ReactionType | None = None,
) -> dict[str, list[PostReactorProfile]]:
    grouped = group_post_reactor_profiles(reactors)
    if reaction_type is None:
        return grouped.model_dump()
    key = format_user_reaction(reaction_type)
    return {key: getattr(grouped, key, [])}
