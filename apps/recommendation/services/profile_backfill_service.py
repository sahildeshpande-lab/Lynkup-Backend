from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.feed.db_models.post_db_model import Post
from apps.profiles.db_models.profile_db_model import Profile
from apps.recommendation.services.post_keyword_service import (
    build_post_plain_text,
    build_post_recommendation_payload,
)
from common.enums import PostState
from core.database.session import async_session_factory

logger = logging.getLogger(__name__)


@dataclass
class _BackfillStats:
    total_users: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _keyword_count(record: dict[str, Any]) -> int:
    count = 0
    for field in ("major", "minor", "interests"):
        values = record.get(field) or []
        if isinstance(values, list):
            count += len(values)
    for field in ("hashtags", "engagement_keywords", "content_keywords"):
        values = record.get(field) or {}
        if isinstance(values, dict):
            count += len(values)
    return count


class ProfileBackfillService:
    """One-time utility to populate ``profiles.extracted_keywords`` for legacy users.

    Not an API endpoint and not scheduled — invoke manually after deployment.
    """

    async def backfill_existing_profiles(self) -> None:
        """Backfill every profile whose ``extracted_keywords`` is still NULL."""
        started = time.perf_counter()
        stats = _BackfillStats()

        logger.info("[profile-backfill]\nStarted")

        async with async_session_factory() as session:
            profile_ids = await self._get_null_keyword_profile_ids(session)

        stats.total_users = len(profile_ids)

        for user_id in profile_ids:
            try:
                async with async_session_factory() as session:
                    result = await self._backfill_one_user(session, user_id=user_id)

                if result["status"] == "skipped":
                    stats.skipped += 1
                else:
                    stats.updated += 1

                logger.info(
                    "[profile-backfill]\nuser_id=%s\nposts_processed=%s\n"
                    "keywords_generated=%s\nstatus=%s",
                    user_id,
                    result["posts_processed"],
                    result["keywords_generated"],
                    result["status"],
                )
            except Exception:
                stats.failed += 1
                logger.exception(
                    "[profile-backfill]\nuser_id=%s\nstatus=failed",
                    user_id,
                )

        duration_seconds = time.perf_counter() - started
        logger.info(
            "[profile-backfill]\ntotal_users=%s\nupdated=%s\nskipped=%s\n"
            "failed=%s\nduration_seconds=%.3f",
            stats.total_users,
            stats.updated,
            stats.skipped,
            stats.failed,
            duration_seconds,
        )

    async def _get_null_keyword_profile_ids(self, session: AsyncSession) -> list[UUID]:
        rows = (
            await session.execute(
                select(Profile.user_id).where(Profile.extracted_keywords.is_(None))
            )
        ).scalars().all()
        return list(rows)

    async def _get_published_posts(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> list[Post]:
        rows = (
            await session.execute(
                select(Post)
                .where(
                    Post.author_user_id == user_id,
                    Post.state == PostState.published,
                )
                .order_by(Post.created_at.asc())
            )
        ).scalars().all()
        return list(rows)

    def _combine_post_text(self, posts: list[Post]) -> str:
        parts: list[str] = []
        for post in posts:
            text = build_post_plain_text(post.content)
            if text:
                parts.append(text)
        return "\n\n".join(parts).strip()

    async def _build_extracted_keywords(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        combined_text: str,
    ) -> dict[str, Any]:
        """Reuse the post-create keyword pipeline on combined published post text."""
        content = {"caption": combined_text} if combined_text else None
        payload = await build_post_recommendation_payload(
            session,
            user_id=user_id,
            content=content,
        )
        payload.pop("_post_snapshot", None)

        return {
            "major": list(payload.get("major") or []),
            "minor": list(payload.get("minor") or []),
            "interests": list(payload.get("interests") or []),
            "hashtags": dict(payload.get("hashtags") or {}),
            "engagement_keywords": {},
            "content_keywords": dict(payload.get("content_keywords") or {}),
        }

    async def _backfill_one_user(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> dict[str, Any]:
        profile = (
            await session.execute(select(Profile).where(Profile.user_id == user_id))
        ).scalar_one_or_none()

        if profile is None:
            raise ValueError(f"Profile not found for user_id={user_id}")

        # Skip if keywords were populated since the initial NULL scan.
        if profile.extracted_keywords is not None:
            return {
                "status": "skipped",
                "posts_processed": 0,
                "keywords_generated": 0,
            }

        posts = await self._get_published_posts(session, user_id=user_id)
        combined_text = self._combine_post_text(posts)
        record = await self._build_extracted_keywords(
            session,
            user_id=user_id,
            combined_text=combined_text,
        )

        profile.extracted_keywords = record
        profile.keywords_updated_at = _utc_now()
        # Intentionally leave learning_recommendations / recommendations_updated_at alone.
        session.add(profile)
        await session.commit()

        return {
            "status": "updated",
            "posts_processed": len(posts),
            "keywords_generated": _keyword_count(record),
        }
