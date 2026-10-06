"""Learning Spotlight – Persistence & Engagement Service.

Handles:
1. Idempotent persistence of selected Learning Spotlight papers on ``profiles.learning_spotlight``.
2. Historical archiving of replaced V2 spotlights to ``learning_recommendation_logs``.
3. Per-paper engagement state mutations (mark read, save/unsave, rating feedback) while
   preserving all paper metadata, query, score, and cycle information.
"""

from __future__ import annotations

import copy
import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from apps.learningspotlight.db_models.learning_paper_interaction_db_model import (
    LearningPaperInteraction,
)
from apps.learningspotlight.schemas import (
    LearningSpotlight,
    LearningSpotlightAuthor,
    LearningSpotlightEngagement,
    LearningSpotlightPaper,
    SavedPaperItem,
    ScoredSpotlightCandidate,
)
from apps.learningspotlight.services.cycle_service import (
    has_spotlight_for_cycle_day,
    parse_generated_at,
)
from apps.profiles.db_models.learning_recommendation_log_db_model import (
    LearningRecommendationLog,
)
from apps.profiles.db_models.profile_db_model import Profile
from common.enums import LearningPaperAction, SpotlightFeedback, SpotlightType
from sqlalchemy import desc, func, over

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_todays_spotlight(
    snapshot: dict[str, Any] | None,
    *,
    today: date | None = None,
) -> bool:
    """True when the snapshot is V2 and ``generated_at`` falls on ``today`` (UTC)."""
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("version") != 2:
        return False
    generated_at = parse_generated_at(snapshot.get("generated_at"))
    if generated_at is None:
        return False
    return generated_at.date() == (today or _utc_now().date())


def _normalize_papers_in_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Ensure snapshot has a valid list of paper dicts with per-paper engagement."""
    if isinstance(snapshot.get("papers"), list) and snapshot["papers"]:
        papers = []
        for p in snapshot["papers"]:
            if isinstance(p, dict):
                p_copy = copy.deepcopy(p)
                if not isinstance(p_copy.get("engagement"), dict):
                    p_copy["engagement"] = {"is_read": False, "is_saved": False, "feedback": False}
                papers.append(p_copy)
        return papers

    # Legacy single-paper format
    if isinstance(snapshot.get("paper"), dict):
        p = copy.deepcopy(snapshot["paper"])
        eng = snapshot.get("engagement") or {"is_read": False, "is_saved": False, "feedback": False}
        p["engagement"] = copy.deepcopy(eng) if isinstance(eng, dict) else {"is_read": False, "is_saved": False, "feedback": False}
        if "score" in snapshot and "score" not in p:
            p["score"] = snapshot["score"]
        return [p]

    return []


class SpotlightPersistenceService:
    """Service managing database persistence and engagement for Learning Spotlight."""

    async def persist_selected_spotlight(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        candidate: ScoredSpotlightCandidate | None = None,
        candidates: list[ScoredSpotlightCandidate] | None = None,
        cycle_day: int,
        spotlight_type: SpotlightType,
        today: date | None = None,
        force_regenerate: bool = False,
    ) -> tuple[LearningSpotlight | None, bool]:
        """Persist the selected candidate(s) spotlight on the user's profile.

        Idempotency:
        - If today's V2 spotlight for this ``cycle_day`` and ``spotlight_type`` already
          exists, it is NOT overwritten. Returns ``(existing_spotlight, False)``.
        - If today's snapshot is for a different ``spotlight_type`` (admin remapped the
          cycle), the previous snapshot is archived and replaced.

        Archiving:
        - If a previous V2 spotlight exists from an earlier date/cycle, or today's
          snapshot is a different type, it is archived to
          ``learning_recommendation_logs`` before replacement.

        Fresh Engagement:
        - Each new paper starts with ``is_read=False, is_saved=False, feedback=None``.

        Returns
        -------
        tuple[LearningSpotlight | None, bool]
            ``(spotlight, is_new)`` where ``is_new=True`` if a new spotlight was written,
            or ``is_new=False`` if generation was skipped due to idempotency.
        """
        run_today = today or _utc_now().date()

        # Normalize candidates input
        selected_candidates: list[ScoredSpotlightCandidate] = []
        if candidates is not None:
            selected_candidates = list(candidates)
        elif candidate is not None:
            selected_candidates = [candidate]

        if not selected_candidates:
            logger.warning("[spotlight-persist] No candidates provided for user %s", user_id)
            return None, False

        # Load profile
        stmt = select(Profile).where(Profile.user_id == user_id)
        result = await session.execute(stmt)
        profile = result.scalar_one_or_none()

        if profile is None:
            logger.warning("[spotlight-persist] Profile not found for user %s", user_id)
            return None, False

        current_snapshot = profile.learning_spotlight

        # 1. Idempotency: already generated today for this cycle day and type?
        if not force_regenerate and has_spotlight_for_cycle_day(
            current_snapshot,
            cycle_day=cycle_day,
            today=run_today,
            spotlight_type=spotlight_type,
        ):
            logger.info(
                "[spotlight-persist] Idempotent skip: user %s already has %s for day %d",
                user_id,
                spotlight_type.value,
                cycle_day,
            )
            try:
                existing = LearningSpotlight.model_validate(current_snapshot)
                return existing, False
            except Exception:  # nosec B110 -- best-effort duplicate check
                pass

        # 2. Archive previous V2 spotlight if one exists
        if isinstance(current_snapshot, dict) and current_snapshot.get("version") == 2:
            log_entry = LearningRecommendationLog(
                user_id=user_id,
                learning_recommendations=dict(current_snapshot),
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
            session.add(log_entry)
            logger.info(
                "[spotlight-persist] Archived previous V2 spotlight for user %s to logs",
                user_id,
            )

        # 3. Construct new LearningSpotlight with fresh per-paper engagement
        now_dt = _utc_now()
        papers_data: list[LearningSpotlightPaper] = [
            LearningSpotlightPaper(
                paper_id=c.paper_id,
                title=c.title,
                authors=c.authors,
                abstract=c.abstract,
                venue=c.venue,
                year=c.year,
                citation_count=c.citation_count,
                url=c.url,
                score=c.final_score,
                engagement=LearningSpotlightEngagement(
                    is_read=False,
                    is_saved=False,
                    feedback=False,
                ),
            )


            for c in selected_candidates
        ]

        new_spotlight = LearningSpotlight(
            version=2,
            cycle_day=cycle_day,
            spotlight_type=spotlight_type,
            query=selected_candidates[0].query,
            papers=papers_data,
            generated_at=now_dt,
        )

        # 4. Save to Profile
        spotlight_json = new_spotlight.model_dump(mode="json", exclude={"description"})
        profile.learning_spotlight = spotlight_json
        profile.learning_spotlight_updated_at = now_dt
        profile.updated_at = now_dt
        flag_modified(profile, "learning_spotlight")

        session.add(profile)
        await session.commit()
        await session.refresh(profile)

        logger.info(
            "[spotlight-persist] Successfully persisted spotlight for user %s (day=%d, type=%s, papers_count=%d)",
            user_id,
            cycle_day,
            spotlight_type.value,
            len(papers_data),
        )
        return new_spotlight, True

    async def get_current_spotlight(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> LearningSpotlight | None:
        """Retrieve the user's active Learning Spotlight for today (UTC, read-only).

        Previous-day snapshots remain on the profile until cron replaces them,
        but are not returned as the active spotlight.
        """
        stmt = select(Profile).where(Profile.user_id == user_id)
        result = await session.execute(stmt)
        profile = result.scalar_one_or_none()

        if profile is None or not _is_todays_spotlight(profile.learning_spotlight):
            return None

        try:
            return LearningSpotlight.model_validate(profile.learning_spotlight)
        except Exception as exc:
            logger.warning(
                "[spotlight-get] Failed to parse spotlight for user %s: %s",
                user_id,
                exc,
            )
            return None

    async def update_read_status(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        is_read: bool,
        paper_id: str | None = None,
        read_time_seconds: int | None = None,
    ) -> LearningSpotlight:
        """Update engagement.is_read on the matched paper and insert a READ event.

        ``paper_id`` is resolved against the current snapshot first, then archived
        V2 snapshots. An explicit ``paper_id`` that matches neither is 404 — it is
        never silently attributed to ``papers[0]``.
        """
        profile = await self._get_profile_with_active_spotlight(session, user_id)

        snapshot = copy.deepcopy(profile.learning_spotlight)
        papers = _normalize_papers_in_snapshot(snapshot)
        if not papers:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No papers found in active Learning Spotlight.",
            )

        requested_id = str(paper_id).strip() if paper_id else None
        target_paper: dict[str, Any] | None = None
        matched_snapshot = snapshot
        mutate_current = False

        if requested_id:
            for p in papers:
                if str(p.get("paper_id") or "").strip() == requested_id:
                    target_paper = p
                    mutate_current = True
                    break
            if target_paper is None:
                historical = await self._find_paper_in_historical_snapshots(
                    session, user_id, requested_id
                )
                if historical is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Paper not found in current or historical Learning Spotlight.",
                    )
                matched_snapshot, target_paper = historical
        else:
            target_paper = papers[0]
            mutate_current = True

        now_dt = _utc_now()
        if mutate_current:
            target_paper["engagement"]["is_read"] = is_read
            snapshot["papers"] = papers
            profile.learning_spotlight = snapshot
            profile.learning_spotlight_updated_at = now_dt
            profile.updated_at = now_dt
            flag_modified(profile, "learning_spotlight")
            session.add(profile)

        effective_paper_id = str(target_paper.get("paper_id") or "").strip()
        raw_spot_id = matched_snapshot.get("id") or matched_snapshot.get("spotlight_id")
        spotlight_id: UUID | None = None
        if raw_spot_id:
            try:
                spotlight_id = UUID(str(raw_spot_id))
            except Exception:
                spotlight_id = None

        if effective_paper_id and is_read:
            interaction = LearningPaperInteraction(
                user_id=user_id,
                paper_id=effective_paper_id,
                spotlight_id=spotlight_id,
                action=LearningPaperAction.read.value,
                read_time_seconds=read_time_seconds,
                created_at=now_dt,
            )
            session.add(interaction)

        await session.commit()
        if mutate_current:
            await session.refresh(profile)

        return LearningSpotlight.model_validate(profile.learning_spotlight)

    async def _find_paper_in_historical_snapshots(
        self,
        session: AsyncSession,
        user_id: UUID,
        paper_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Return ``(snapshot, paper)`` from archived V2 spotlight logs."""
        stmt = (
            select(LearningRecommendationLog)
            .where(LearningRecommendationLog.user_id == user_id)
            .order_by(desc(LearningRecommendationLog.created_at))
        )
        logs = (await session.execute(stmt)).scalars().all()
        for log in logs:
            rec = log.learning_recommendations
            if not isinstance(rec, dict) or rec.get("version") != 2:
                continue
            for paper in _normalize_papers_in_snapshot(rec):
                if str(paper.get("paper_id") or "").strip() == paper_id:
                    return rec, paper
        return None

    async def update_save_status(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        is_saved: bool,
        paper_id: str | None = None,
    ) -> LearningSpotlight:
        """Update engagement.is_saved and insert a SAVE/UNSAVE event.

        ``paper_id`` may refer to a paper on the current cycle or a previously
        saved paper from an earlier cycle. Historical papers are unsaved via
        an interaction event only — the current cycle snapshot is not mutated
        and ``papers[0]`` is never used as a fallback when ``paper_id`` is sent.
        """
        profile = await self._get_profile_with_active_spotlight(session, user_id)

        snapshot = copy.deepcopy(profile.learning_spotlight)
        papers = _normalize_papers_in_snapshot(snapshot)
        requested_id = str(paper_id).strip() if paper_id else None

        target_paper: dict[str, Any] | None = None
        if requested_id:
            for p in papers:
                if str(p.get("paper_id") or "").strip() == requested_id:
                    target_paper = p
                    break
        elif papers:
            target_paper = papers[0]

        now_dt = _utc_now()
        spotlight_id: UUID | None = None
        mutate_current = target_paper is not None
        effective_paper_id = ""

        if mutate_current:
            target_paper["engagement"]["is_saved"] = is_saved
            snapshot["papers"] = papers
            profile.learning_spotlight = snapshot
            profile.learning_spotlight_updated_at = now_dt
            profile.updated_at = now_dt
            flag_modified(profile, "learning_spotlight")
            session.add(profile)
            effective_paper_id = str(target_paper.get("paper_id") or "").strip()
            raw_spot_id = snapshot.get("id") or snapshot.get("spotlight_id")
            if raw_spot_id:
                try:
                    spotlight_id = UUID(str(raw_spot_id))
                except Exception:
                    spotlight_id = None
        elif requested_id:
            if not is_saved and not await self._is_paper_currently_saved(
                session, user_id, requested_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Paper is not in the user's saved list.",
                )
            effective_paper_id = requested_id
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No papers found in active Learning Spotlight.",
            )

        if effective_paper_id:
            action = (
                LearningPaperAction.save.value
                if is_saved
                else LearningPaperAction.unsave.value
            )
            session.add(
                LearningPaperInteraction(
                    user_id=user_id,
                    paper_id=effective_paper_id,
                    spotlight_id=spotlight_id,
                    action=action,
                    created_at=now_dt,
                )
            )

        await session.commit()
        if mutate_current:
            await session.refresh(profile)

        return LearningSpotlight.model_validate(profile.learning_spotlight)

    async def submit_feedback(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        feedback: SpotlightFeedback | bool | str,
        paper_id: str | None = None,
    ) -> LearningSpotlight:
        """Update engagement.feedback on the specific paper and insert LIKE/DISLIKE event."""
        profile = await self._get_profile_with_active_spotlight(session, user_id)

        snapshot = copy.deepcopy(profile.learning_spotlight)
        papers = _normalize_papers_in_snapshot(snapshot)
        if not papers:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No papers found in active Learning Spotlight.",
            )

        # Find target paper
        target_paper: dict[str, Any] | None = None
        if paper_id:
            for p in papers:
                if str(p.get("paper_id")).strip() == str(paper_id).strip():
                    target_paper = p
                    break
        if target_paper is None:
            target_paper = papers[0]

        target_paper["engagement"]["feedback"] = False if feedback in (False, "false", "0") else True
        snapshot["papers"] = papers

        now_dt = _utc_now()
        profile.learning_spotlight = snapshot
        profile.learning_spotlight_updated_at = now_dt
        profile.updated_at = now_dt
        flag_modified(profile, "learning_spotlight")

        session.add(profile)

        effective_paper_id = str(target_paper.get("paper_id") or "").strip()
        raw_spot_id = snapshot.get("id") or snapshot.get("spotlight_id")
        spotlight_id: UUID | None = None
        if raw_spot_id:
            try:
                spotlight_id = UUID(str(raw_spot_id))
            except Exception:
                spotlight_id = None

        if effective_paper_id:
            if feedback in (
                False,
                "false",
                "not_useful",
                SpotlightFeedback.not_useful,
                "dislike",
                "disliked",
            ):
                action = LearningPaperAction.dislike.value
            else:
                action = LearningPaperAction.like.value

            interaction = LearningPaperInteraction(
                user_id=user_id,
                paper_id=effective_paper_id,
                spotlight_id=spotlight_id,
                action=action,
                created_at=now_dt,
            )
            session.add(interaction)

        await session.commit()
        await session.refresh(profile)

        return LearningSpotlight.model_validate(profile.learning_spotlight)


    async def get_saved_papers(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> list[SavedPaperItem]:
        """Retrieve all currently saved papers for user where latest interaction is SAVE."""
        rn = over(
            func.row_number(),
            partition_by=LearningPaperInteraction.paper_id,
            order_by=desc(LearningPaperInteraction.created_at),
        ).label("rn")

        subquery = (
            select(
                LearningPaperInteraction.paper_id,
                LearningPaperInteraction.action,
                LearningPaperInteraction.created_at,
                rn,
            )
            .where(
                LearningPaperInteraction.user_id == user_id,
                LearningPaperInteraction.action.in_(
                    [LearningPaperAction.save.value, LearningPaperAction.unsave.value]
                ),
            )
            .subquery()
        )

        stmt = (
            select(
                subquery.c.paper_id,
                subquery.c.created_at,
            )
            .where(
                subquery.c.rn == 1,
                subquery.c.action == LearningPaperAction.save.value,
            )
            .order_by(desc(subquery.c.created_at))
        )

        res = await session.execute(stmt)
        saved_rows = res.all()
        if not saved_rows:
            return []

        # Resolve paper metadata from Profile.learning_spotlight and archived logs
        profile_stmt = select(Profile).where(Profile.user_id == user_id)
        profile_res = await session.execute(profile_stmt)
        profile = profile_res.scalar_one_or_none()

        paper_metadata_map: dict[str, dict[str, Any]] = {}

        if profile and isinstance(profile.learning_spotlight, dict):
            # Check papers array
            for p in profile.learning_spotlight.get("papers") or []:
                if isinstance(p, dict) and p.get("paper_id"):
                    paper_metadata_map[str(p["paper_id"]).strip()] = p
            # Check legacy paper
            legacy_p = profile.learning_spotlight.get("paper")
            if isinstance(legacy_p, dict) and legacy_p.get("paper_id"):
                paper_metadata_map[str(legacy_p["paper_id"]).strip()] = legacy_p

        logs_stmt = (
            select(LearningRecommendationLog)
            .where(LearningRecommendationLog.user_id == user_id)
            .order_by(desc(LearningRecommendationLog.created_at))
        )
        logs_res = await session.execute(logs_stmt)
        logs = logs_res.scalars().all()

        for log in logs:
            rec = log.learning_recommendations
            if isinstance(rec, dict):
                for p in rec.get("papers") or []:
                    if isinstance(p, dict) and p.get("paper_id") and str(p["paper_id"]).strip() not in paper_metadata_map:
                        paper_metadata_map[str(p["paper_id"]).strip()] = p
                legacy_p = rec.get("paper")
                if isinstance(legacy_p, dict) and legacy_p.get("paper_id") and str(legacy_p["paper_id"]).strip() not in paper_metadata_map:
                    paper_metadata_map[str(legacy_p["paper_id"]).strip()] = legacy_p

        saved_papers: list[SavedPaperItem] = []
        for paper_id_val, saved_at_dt in saved_rows:
            norm_pid = str(paper_id_val).strip()
            meta = paper_metadata_map.get(norm_pid, {})
            raw_authors = meta.get("authors", [])
            authors: list[LearningSpotlightAuthor] = []
            for a in raw_authors:
                if isinstance(a, dict):
                    authors.append(LearningSpotlightAuthor.model_validate(a))
                elif isinstance(a, str):
                    authors.append(LearningSpotlightAuthor(name=a))

            saved_papers.append(
                SavedPaperItem(
                    paper_id=norm_pid,
                    title=meta.get("title") or norm_pid,
                    authors=authors,
                    abstract=meta.get("abstract"),
                    venue=meta.get("venue"),
                    year=meta.get("year"),
                    citation_count=meta.get("citation_count"),
                    url=meta.get("url"),
                    saved_at=saved_at_dt,
                )
            )

        return saved_papers

    async def has_user_read_paper(
        self,
        session: AsyncSession,
        user_id: UUID,
        paper_id: str,
    ) -> bool:
        """Check if user has a READ interaction event for this paper."""
        stmt = (
            select(func.count())
            .select_from(LearningPaperInteraction)
            .where(
                LearningPaperInteraction.user_id == user_id,
                LearningPaperInteraction.paper_id == paper_id,
                LearningPaperInteraction.action == LearningPaperAction.read.value,
            )
        )
        res = await session.execute(stmt)
        count = res.scalar_one_or_none() or 0
        return count > 0

    async def has_user_saved_paper(
        self,
        session: AsyncSession,
        user_id: UUID,
        paper_id: str,
    ) -> bool:
        """Check if user's latest interaction for paper is SAVE."""
        return await self._is_paper_currently_saved(session, user_id, paper_id)

    async def _is_paper_currently_saved(
        self,
        session: AsyncSession,
        user_id: UUID,
        paper_id: str,
    ) -> bool:
        """True when the latest SAVE/UNSAVE event for this paper is SAVE."""
        stmt = (
            select(LearningPaperInteraction.action)
            .where(
                LearningPaperInteraction.user_id == user_id,
                LearningPaperInteraction.paper_id == paper_id,
                LearningPaperInteraction.action.in_(
                    [LearningPaperAction.save.value, LearningPaperAction.unsave.value]
                ),
            )
            .order_by(desc(LearningPaperInteraction.created_at))
            .limit(1)
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none() == LearningPaperAction.save.value

    async def has_user_seen_paper(
        self,
        session: AsyncSession,
        user_id: UUID,
        paper_id: str,
    ) -> bool:
        """Check if user has any interaction recorded for paper."""
        stmt = (
            select(func.count())
            .select_from(LearningPaperInteraction)
            .where(
                LearningPaperInteraction.user_id == user_id,
                LearningPaperInteraction.paper_id == paper_id,
            )
        )
        res = await session.execute(stmt)
        count = res.scalar_one_or_none() or 0
        return count > 0

    async def _get_profile_with_active_spotlight(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> Profile:
        """Helper to fetch profile and verify today's V2 spotlight exists."""
        stmt = select(Profile).where(Profile.user_id == user_id)
        result = await session.execute(stmt)
        profile = result.scalar_one_or_none()

        if profile is None or not _is_todays_spotlight(profile.learning_spotlight):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active Learning Spotlight found for user.",
            )

        return profile
