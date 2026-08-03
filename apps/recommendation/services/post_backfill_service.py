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
from apps.recommendation.services.post_keyword_service import (
    build_post_recommendation_payload,
)
from common.enums import PostState
from core.database.session import async_session_factory

logger = logging.getLogger(__name__)

_EMPTY_SNAPSHOT: dict[str, dict[str, int]] = {
    "hashtags": {},
    "content_keywords": {},
}


@dataclass
class _BackfillStats:
    total_posts: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _keyword_count(snapshot: dict[str, Any]) -> int:
    count = 0
    for field in ("hashtags", "content_keywords"):
        values = snapshot.get(field) or {}
        if isinstance(values, dict):
            count += len(values)
    return count


class PostBackfillService:
    """One-time utility to populate ``posts.extracted_keywords`` for legacy posts.

    Not an API endpoint and not scheduled — invoke manually after deployment.
    """

    async def backfill_existing_posts(self) -> None:
        """Backfill every published post whose ``extracted_keywords`` is still NULL."""
        started = time.perf_counter()
        stats = _BackfillStats()

        logger.info("[post-backfill]\nStarted")

        async with async_session_factory() as session:
            post_ids = await self._get_eligible_post_ids(session)

        stats.total_posts = len(post_ids)

        for post_id in post_ids:
            try:
                async with async_session_factory() as session:
                    result = await self._backfill_one_post(session, post_id=post_id)

                if result["status"] == "skipped":
                    stats.skipped += 1
                else:
                    stats.updated += 1

                logger.info(
                    "[post-backfill]\npost_id=%s\nuser_id=%s\nstatus=%s\n"
                    "keywords_generated=%s",
                    post_id,
                    result.get("user_id"),
                    result["status"],
                    result["keywords_generated"],
                )
            except Exception:
                stats.failed += 1
                logger.exception(
                    "[post-backfill]\npost_id=%s\nstatus=failed",
                    post_id,
                )

        duration_seconds = time.perf_counter() - started
        logger.info(
            "[post-backfill]\ntotal_posts=%s\nupdated=%s\nskipped=%s\n"
            "failed=%s\nduration_seconds=%.3f",
            stats.total_posts,
            stats.updated,
            stats.skipped,
            stats.failed,
            duration_seconds,
        )

    async def _get_eligible_post_ids(self, session: AsyncSession) -> list[UUID]:
        rows = (
            await session.execute(
                select(Post.id).where(
                    Post.extracted_keywords.is_(None),
                    Post.state == PostState.published,
                )
            )
        ).scalars().all()
        return list(rows)

    async def _build_post_snapshot(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        content: dict | None,
    ) -> dict[str, Any]:
        """Reuse the post-create extraction pipeline; keep only the per-post snapshot."""
        payload = await build_post_recommendation_payload(
            session,
            user_id=user_id,
            content=content,
        )
        snapshot = payload.pop("_post_snapshot", None)
        if not isinstance(snapshot, dict):
            return dict(_EMPTY_SNAPSHOT)

        return {
            "hashtags": dict(snapshot.get("hashtags") or {}),
            "content_keywords": dict(snapshot.get("content_keywords") or {}),
        }

    async def _backfill_one_post(
        self,
        session: AsyncSession,
        *,
        post_id: UUID,
    ) -> dict[str, Any]:
        post = (
            await session.execute(select(Post).where(Post.id == post_id))
        ).scalar_one_or_none()

        if post is None:
            raise ValueError(f"Post not found for post_id={post_id}")

        user_id = post.author_user_id

        # Skip if keywords were populated since the initial NULL scan, or state changed.
        if post.extracted_keywords is not None or post.state != PostState.published:
            return {
                "status": "skipped",
                "user_id": user_id,
                "keywords_generated": 0,
            }

        snapshot = await self._build_post_snapshot(
            session,
            user_id=user_id,
            content=post.content,
        )

        post.extracted_keywords = snapshot
        # Model/DB column is ``keywords_updated_at`` (not extracted_keywords_updated_at).
        post.keywords_updated_at = _utc_now()
        session.add(post)
        await session.commit()

        return {
            "status": "updated",
            "user_id": user_id,
            "keywords_generated": _keyword_count(snapshot),
        }
