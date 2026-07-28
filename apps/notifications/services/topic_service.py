from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from firebase_admin import messaging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.notifications.repositories.campaign_audience_repository import (
    get_active_fcm_tokens_for_users,
)
from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.country_db_model import Country
from apps.profiles.db_models.profile_db_model import Profile
from apps.profiles.db_models.university_db_model import University
from common.enums import NotificationTargetType
from core.auth.firebase_app import initialize_firebase_app

logger = logging.getLogger(__name__)

# Firebase topic names must match [a-zA-Z0-9-_.~%]+ and stay under 900 chars.
_TOPIC_MAX_LENGTH = 900
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")

TopicBuilder = Callable[[AsyncSession, Profile], Awaitable[set[str]]]


def slugify_topic_value(value: str) -> str:
    """Normalize a free-text value into a Firebase-safe lowercase slug."""
    text = (value or "").strip().lower()
    if not text:
        return ""
    text = _NON_ALNUM_RE.sub("_", text)
    text = _MULTI_UNDERSCORE_RE.sub("_", text).strip("_")
    return text[:_TOPIC_MAX_LENGTH]


def format_topic(prefix: str, value: str) -> str | None:
    slug = slugify_topic_value(value)
    if not slug:
        return None
    topic = f"{prefix}_{slug}"
    return topic[:_TOPIC_MAX_LENGTH]


_TARGET_TYPE_PREFIX: dict[NotificationTargetType, str] = {
    NotificationTargetType.university: "university",
    NotificationTargetType.major: "major",
    NotificationTargetType.minor: "minor",
    NotificationTargetType.interests: "interest",
    NotificationTargetType.education_level: "education_level",
    NotificationTargetType.country: "country",
    NotificationTargetType.hashtags: "hashtag",
}


async def resolve_firebase_topics_from_targets(
    db: AsyncSession,
    targets: list[tuple[NotificationTargetType, list[str]]],
) -> set[str]:
    """
    Map campaign TOPIC targets to Firebase topic names.

    Uses the same slug convention as user topic subscriptions so dispatch can
    publish directly without resolving individual users.
    """
    topics: set[str] = set()
    for target_type, values in targets:
        prefix = _TARGET_TYPE_PREFIX.get(target_type)
        if not prefix:
            continue
        for raw in values:
            value = str(raw).strip()
            if not value:
                continue
            resolved = await _resolve_target_value_for_topic(db, target_type, value)
            if not resolved:
                continue
            topic = format_topic(prefix, resolved)
            if topic:
                topics.add(topic)
    return topics


async def _resolve_target_value_for_topic(
    db: AsyncSession,
    target_type: NotificationTargetType,
    value: str,
) -> str | None:
    if target_type == NotificationTargetType.university:
        try:
            university_id = UUID(value)
        except (TypeError, ValueError):
            university_id = None
        if university_id is not None:
            university = (
                await db.execute(select(University).where(University.id == university_id))
            ).scalar_one_or_none()
            if university is None:
                return None
            return (university.slug or university.name or "").strip() or None
        return value

    if target_type == NotificationTargetType.interests:
        if value.isdigit():
            interest = (
                await db.execute(
                    select(AcademicInterest).where(AcademicInterest.id == int(value))
                )
            ).scalar_one_or_none()
            return interest.name if interest else None
        return value

    if target_type == NotificationTargetType.country:
        try:
            country_id = UUID(value)
        except (TypeError, ValueError):
            country_id = None
        if country_id is not None:
            country = (
                await db.execute(select(Country).where(Country.id == country_id))
            ).scalar_one_or_none()
            return country.name if country else None
        return value

    if target_type == NotificationTargetType.hashtags:
        from apps.feed.db_models.hashtag_db_model import Hashtag

        raw = value.lstrip("#").strip()
        if not raw:
            return None
        try:
            hashtag_id = UUID(raw)
        except (TypeError, ValueError):
            hashtag_id = None
        if hashtag_id is not None:
            hashtag = (
                await db.execute(select(Hashtag).where(Hashtag.id == hashtag_id))
            ).scalar_one_or_none()
            return ((hashtag.tag or "").strip().lower() or None) if hashtag else None
        return raw.lower()

    return value


async def _topics_from_university(db: AsyncSession, profile: Profile) -> set[str]:
    if not profile.university_id:
        return set()
    university = (
        await db.execute(select(University).where(University.id == profile.university_id))
    ).scalar_one_or_none()
    if university is None:
        return set()
    raw = (university.slug or university.name or "").strip()
    topic = format_topic("university", raw)
    return {topic} if topic else set()


async def _topics_from_major(_db: AsyncSession, profile: Profile) -> set[str]:
    topic = format_topic("major", profile.major or "")
    return {topic} if topic else set()


async def _topics_from_minor(_db: AsyncSession, profile: Profile) -> set[str]:
    topic = format_topic("minor", profile.minor or "")
    return {topic} if topic else set()


async def _topics_from_hashtags(db: AsyncSession, profile: Profile) -> set[str]:
    from apps.feed.db_models.hashtag_db_model import Hashtag
    from apps.feed.db_models.post_db_model import Post
    from apps.feed.db_models.post_hashtag_db_model import PostHashtag
    from common.enums import PostState

    rows = (
        await db.execute(
            select(Hashtag.tag)
            .join(PostHashtag, PostHashtag.hashtag_id == Hashtag.id)
            .join(Post, Post.id == PostHashtag.post_id)
            .where(
                Post.author_user_id == profile.user_id,
                Post.state == PostState.published,
            )
            .distinct()
        )
    ).scalars().all()

    topics: set[str] = set()
    for tag in rows:
        topic = format_topic("hashtag", tag or "")
        if topic:
            topics.add(topic)
    return topics


async def _topics_from_interests(db: AsyncSession, profile: Profile) -> set[str]:
    interest_ids = [
        int(interest_id)
        for interest_id in (profile.profile_interests_id or [])
        if interest_id is not None
    ]
    if not interest_ids:
        return set()

    rows = (
        await db.execute(
            select(AcademicInterest).where(
                AcademicInterest.id.in_(interest_ids),
                AcademicInterest.is_active.is_(True),
            )
        )
    ).scalars().all()

    topics: set[str] = set()
    for interest in rows:
        topic = format_topic("interest", interest.name or "")
        if topic:
            topics.add(topic)
    return topics


# Append new builders here (year, club, department, …) without changing sync logic.
_TOPIC_BUILDERS: list[TopicBuilder] = [
    _topics_from_university,
    _topics_from_major,
    _topics_from_minor,
    _topics_from_interests,
    _topics_from_hashtags,
]

# Payload fields that affect Firebase topic membership.
_TOPIC_PAYLOAD_FIELDS: tuple[str, ...] = (
    "major",
    "minor",
    "university_id",
    "academic_interests",
)


class TopicService:
    """
    Single source of truth for Firebase topic naming and device subscription sync.

    Frontend must never call subscribeToTopic / unsubscribeFromTopic — the backend
    manages subscriptions for every active FCM token belonging to the user.

    Call sites (onboarding, user update profile, admin update profile) must only
    use ``capture_topics`` + ``sync_user_topics`` — never duplicate subscribe logic.
    """

    @staticmethod
    def affects_topics(payload: Any) -> bool:
        """True when the update payload includes any topic-related field."""
        return any(
            getattr(payload, field, None) is not None
            for field in _TOPIC_PAYLOAD_FIELDS
        )

    @staticmethod
    async def capture_topics(db: AsyncSession, profile: Profile) -> set[str]:
        """Safely build the current topic set before a profile mutation."""
        try:
            return await TopicService.build_topics(db, profile)
        except Exception:
            logger.exception(
                "Failed to capture Firebase topics user_id=%s",
                getattr(profile, "user_id", None),
            )
            return set()

    @staticmethod
    async def build_topics(db: AsyncSession, profile: Profile) -> set[str]:
        """Build the full expected topic set for a profile via registered builders."""
        topics: set[str] = set()
        for builder in _TOPIC_BUILDERS:
            try:
                topics |= await builder(db, profile)
            except Exception:
                logger.exception(
                    "Topic builder failed builder=%s user_id=%s",
                    getattr(builder, "__name__", builder),
                    profile.user_id,
                )
        return topics

    @staticmethod
    def subscribe(tokens: list[str], topics: set[str]) -> dict[str, Any]:
        """Subscribe every token to each topic. Failures are logged, not raised."""
        return TopicService._apply_topic_operation(
            tokens=tokens,
            topics=topics,
            operation="subscribe",
            api=messaging.subscribe_to_topic,
        )

    @staticmethod
    def unsubscribe(tokens: list[str], topics: set[str]) -> dict[str, Any]:
        """Unsubscribe every token from each topic. Failures are logged, not raised."""
        return TopicService._apply_topic_operation(
            tokens=tokens,
            topics=topics,
            operation="unsubscribe",
            api=messaging.unsubscribe_from_topic,
        )

    @staticmethod
    async def sync_user_topics(
        db: AsyncSession,
        user_id: UUID,
        *,
        old_topics: set[str],
        profile: Profile,
    ) -> dict[str, Any]:
        """
        Single entry point for onboarding, user profile update, and admin profile update.

        Builds the new topic set from ``profile`` (post-commit), diffs against
        ``old_topics``, then subscribe/unsubscribe active FCM tokens. Never raises.
        """
        try:
            new_topics = await TopicService.build_topics(db, profile)
            return await TopicService.sync_topics(
                db,
                user_id,
                old_topics=old_topics,
                new_topics=new_topics,
            )
        except Exception:
            logger.exception(
                "Firebase topic sync failed user_id=%s",
                user_id,
            )
            return {
                "topics_to_subscribe": [],
                "topics_to_unsubscribe": [],
                "token_count": 0,
                "subscribe": None,
                "unsubscribe": None,
            }

    @staticmethod
    async def sync_topics(
        db: AsyncSession,
        user_id: UUID,
        *,
        old_topics: set[str],
        new_topics: set[str],
    ) -> dict[str, Any]:
        """
        Diff topic sets and sync all active FCM tokens for the user.

        Unchanged topics are left alone. Firebase errors never propagate.
        Prefer ``sync_user_topics`` from profile flows.
        """
        topics_to_subscribe = set(new_topics) - set(old_topics)
        topics_to_unsubscribe = set(old_topics) - set(new_topics)

        result: dict[str, Any] = {
            "topics_to_subscribe": sorted(topics_to_subscribe),
            "topics_to_unsubscribe": sorted(topics_to_unsubscribe),
            "token_count": 0,
            "subscribe": None,
            "unsubscribe": None,
        }

        if not topics_to_subscribe and not topics_to_unsubscribe:
            logger.info(
                "Firebase topic sync skipped (no changes) user_id=%s",
                user_id,
            )
            return result

        try:
            tokens = await get_active_fcm_tokens_for_users(db, [user_id])
        except Exception:
            logger.exception(
                "Failed to load active FCM tokens for topic sync user_id=%s",
                user_id,
            )
            return result

        result["token_count"] = len(tokens)
        if not tokens:
            logger.info(
                "Firebase topic sync skipped (no active FCM tokens) user_id=%s",
                user_id,
            )
            return result

        if topics_to_subscribe:
            result["subscribe"] = TopicService.subscribe(tokens, topics_to_subscribe)
        if topics_to_unsubscribe:
            result["unsubscribe"] = TopicService.unsubscribe(
                tokens,
                topics_to_unsubscribe,
            )

        logger.info(
            "Firebase topic sync finished user_id=%s tokens=%s subscribe=%s unsubscribe=%s",
            user_id,
            len(tokens),
            sorted(topics_to_subscribe),
            sorted(topics_to_unsubscribe),
        )
        return result

    @staticmethod
    def _apply_topic_operation(
        *,
        tokens: list[str],
        topics: set[str],
        operation: str,
        api: Callable[..., Any],
    ) -> dict[str, Any]:
        cleaned_tokens = [token.strip() for token in tokens if token and token.strip()]
        cleaned_topics = sorted(topic for topic in topics if topic)
        successful = 0
        failed = 0

        if not cleaned_tokens or not cleaned_topics:
            return {
                "successful_count": 0,
                "failed_count": 0,
                "topics": cleaned_topics,
            }

        try:
            initialize_firebase_app()
        except Exception:
            logger.exception(
                "Firebase init failed before topic %s topics=%s",
                operation,
                cleaned_topics,
            )
            return {
                "successful_count": 0,
                "failed_count": len(cleaned_topics),
                "topics": cleaned_topics,
            }

        for topic in cleaned_topics:
            try:
                response = api(cleaned_tokens, topic)
                failure_count = int(getattr(response, "failure_count", 0) or 0)
                success_count = int(getattr(response, "success_count", 0) or 0)
                if failure_count:
                    failed += 1
                    logger.warning(
                        "Firebase topic %s partial failure topic=%s success=%s failure=%s",
                        operation,
                        topic,
                        success_count,
                        failure_count,
                    )
                else:
                    successful += 1
                    logger.info(
                        "Firebase topic %s ok topic=%s tokens=%s",
                        operation,
                        topic,
                        len(cleaned_tokens),
                    )
            except Exception:
                failed += 1
                logger.exception(
                    "Firebase topic %s failed topic=%s token_count=%s",
                    operation,
                    topic,
                    len(cleaned_tokens),
                )

        return {
            "successful_count": successful,
            "failed_count": failed,
            "topics": cleaned_topics,
        }
