"""Learning Spotlight – Candidate Filtering Service.

Pipeline for validating, deduplicating, and filtering candidate papers
produced by spotlight strategies before scoring and ranking.

Filtering stages
----------------
1. **Valid paper**: Excludes candidates missing required fields (``paper_id``,
   ``title``, ``url``, ``abstract``).
2. **Duplicates**: Deduplicates candidates by normalized ``paper_id``.
3. **Previously shown / read / saved**: Excludes papers that the user has
   already received, read, or saved in prior V2 Learning Spotlight runs.
   (V1 recommendation history is intentionally ignored.)
4. **Category-specific validation**:
   * *Influential Research*: ``citation_count >= MIN_INFLUENTIAL_CITATIONS`` (10).
   * *Latest Research*: publication year within the dynamic 1–2 year window
     (``year >= current_year - max_age_years``).
   * *Country Perspective*: preserves country metadata / author affiliations.
   * *Beyond Your Field*: checks major/minor exclusion signals.
   * *Leading Thinker*: untouched (not implemented).

Returns a structured ``CandidateFilterResult`` containing counts for all
filtering stages and the remaining eligible candidates.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.learningspotlight.schemas import (
    CandidateFilterResult,
    SpotlightCandidate,
    SpotlightUserContext,
    extract_recommended_papers,
)
from apps.learningspotlight.services.influential_research_strategy import (
    MIN_INFLUENTIAL_CITATIONS,
)
from apps.learningspotlight.services.keyword_normalization_service import (
    canonicalize_signal,
)
from apps.learningspotlight.services.language_detection_service import (
    LanguageDetectionService,
)
from apps.learningspotlight.services.latest_research_strategy import (
    LATEST_RESEARCH_MAX_AGE_YEARS,
)
from apps.profiles.db_models.learning_recommendation_log_db_model import (
    LearningRecommendationLog,
)
from apps.profiles.db_models.profile_db_model import Profile
from common.enums import SpotlightType

logger = logging.getLogger(__name__)


def _is_valid_candidate(candidate: SpotlightCandidate) -> bool:
    """Check if candidate has all required fields (paper_id, title, url, abstract)."""
    if not candidate.paper_id or not candidate.paper_id.strip():
        return False
    if not candidate.title or not candidate.title.strip():
        return False
    if not candidate.url or not candidate.url.strip():
        return False
    if not candidate.abstract or not candidate.abstract.strip():
        return False
    return True


def _normalize_paper_id(paper_id: str | None) -> str:
    """Normalize paper ID for comparison (strip whitespace + lowercase)."""
    if paper_id is None:
        return ""
    return str(paper_id).strip().lower()


def _ingest_v2_snapshot_history(
    snapshot: dict[str, Any],
    shown_ids: set[str],
    read_ids: set[str],
    saved_ids: set[str],
) -> None:
    """Add previously recommended paper IDs (and engagement) from one V2 snapshot.

    Supports multi-paper ``papers`` lists and the legacy single ``paper`` field.
    Paper IDs are the uniqueness key; title-only matching is never used.
    """
    if snapshot.get("version") != 2:
        return

    paper_ids, _ = extract_recommended_papers(snapshot)
    for paper_id in paper_ids:
        norm_id = _normalize_paper_id(paper_id)
        if norm_id:
            shown_ids.add(norm_id)

    # Per-paper engagement (current multi-paper format)
    raw_papers = snapshot.get("papers")
    if isinstance(raw_papers, list) and raw_papers:
        for paper in raw_papers:
            if not isinstance(paper, dict):
                continue
            raw_id = paper.get("paper_id")
            if raw_id is None:
                continue
            norm_id = _normalize_paper_id(str(raw_id))
            if not norm_id:
                continue
            eng = paper.get("engagement") or {}
            if isinstance(eng, dict):
                if eng.get("is_read") is True:
                    read_ids.add(norm_id)
                if eng.get("is_saved") is True:
                    saved_ids.add(norm_id)
        return

    # Legacy single-paper engagement (top-level on snapshot)
    paper = snapshot.get("paper") if isinstance(snapshot.get("paper"), dict) else {}
    paper_id = paper.get("paper_id") or snapshot.get("paper_id")
    if not paper_id:
        return
    norm_id = _normalize_paper_id(str(paper_id))
    if not norm_id:
        return
    eng = snapshot.get("engagement") or {}
    if isinstance(eng, dict):
        if eng.get("is_read") is True:
            read_ids.add(norm_id)
        if eng.get("is_saved") is True:
            saved_ids.add(norm_id)


async def get_user_spotlight_history(
    session: AsyncSession,
    user_id: UUID,
) -> tuple[set[str], set[str], set[str]]:
    """Retrieve previously shown, read, and saved paper IDs for a user (V2 only).

    Inspects:
    1. ``Profile.learning_spotlight`` for the user's current spotlight snapshot.
    2. ``LearningRecommendationLog`` for historical V2 spotlight snapshots.
       (V1 logs without version=2 are ignored.)

    Returns
    -------
    tuple[set[str], set[str], set[str]]
        ``(previously_shown_ids, already_read_ids, already_saved_ids)``
        All IDs are normalized via ``strip().lower()``.
    """
    shown_ids: set[str] = set()
    read_ids: set[str] = set()
    saved_ids: set[str] = set()

    # 1. Current spotlight from Profile
    profile_stmt = select(Profile).where(Profile.user_id == user_id)
    profile_result = await session.execute(profile_stmt)
    profile = profile_result.scalar_one_or_none()

    if profile and isinstance(profile.learning_spotlight, dict):
        _ingest_v2_snapshot_history(
            profile.learning_spotlight, shown_ids, read_ids, saved_ids
        )

    # 2. Historical V2 logs from LearningRecommendationLog
    logs_stmt = select(LearningRecommendationLog).where(
        LearningRecommendationLog.user_id == user_id
    )
    logs_result = await session.execute(logs_stmt)
    logs = logs_result.scalars().all()

    for log in logs:
        rec = log.learning_recommendations
        if isinstance(rec, dict):
            _ingest_v2_snapshot_history(rec, shown_ids, read_ids, saved_ids)

    return shown_ids, read_ids, saved_ids


class CandidateFilterService:
    """Service responsible for filtering and deduplicating Spotlight candidates."""

    def filter_candidates(
        self,
        candidates: Sequence[SpotlightCandidate],
        *,
        previously_shown_ids: set[str] | None = None,
        already_read_ids: set[str] | None = None,
        already_saved_ids: set[str] | None = None,
        context: SpotlightUserContext | None = None,
        reference_date: date | None = None,
        max_age_years: int = LATEST_RESEARCH_MAX_AGE_YEARS,
    ) -> CandidateFilterResult:
        """Filter a list of candidate papers through all validation rules.

        Parameters
        ----------
        candidates:
            Raw candidates produced by a strategy.
        previously_shown_ids:
            Paper IDs already presented to the user in past V2 spotlights.
        already_read_ids:
            Paper IDs marked as read by the user in past V2 spotlights.
        already_saved_ids:
            Paper IDs marked as saved by the user in past V2 spotlights.
        context:
            User learning signals context (used for category validation).
        reference_date:
            Target date for dynamic year calculations (defaults to UTC today).
        max_age_years:
            Max age in calendar years for latest research (default 1).

        Returns
        -------
        CandidateFilterResult
            Structured result with diagnostic counts and remaining candidates.
        """
        original_count = len(candidates)
        shown_set = {_normalize_paper_id(pid) for pid in (previously_shown_ids or set()) if pid}
        read_set = {_normalize_paper_id(pid) for pid in (already_read_ids or set()) if pid}
        saved_set = {_normalize_paper_id(pid) for pid in (already_saved_ids or set()) if pid}
        # Drop empty normalized IDs so they cannot match accidentally.
        shown_set.discard("")
        read_set.discard("")
        saved_set.discard("")

        if reference_date is None:
            reference_date = datetime.now(timezone.utc).date()

        duplicate_count = 0
        invalid_count = 0
        language_filtered_count = 0
        previously_shown_count = 0
        previously_read_count = 0
        previously_saved_count = 0
        category_filtered_count = 0

        seen_paper_ids: set[str] = set()
        filtered_candidates: list[SpotlightCandidate] = []

        for candidate in candidates:
            # 1. FILTER: VALID PAPER (paper_id, title, url required)
            if not _is_valid_candidate(candidate):
                invalid_count += 1
                continue

            # 2. FILTER: LANGUAGE DETECTION (must be confidently English)
            if not LanguageDetectionService.is_english(candidate.title, candidate.abstract):
                language_filtered_count += 1
                continue

            norm_id = _normalize_paper_id(candidate.paper_id)

            # 3. FILTER: DUPLICATES
            if norm_id in seen_paper_ids:
                duplicate_count += 1
                continue
            seen_paper_ids.add(norm_id)

            # 4. FILTER: PREVIOUSLY SHOWN / ALREADY READ / ALREADY SAVED
            is_shown = norm_id in shown_set
            is_read = norm_id in read_set
            is_saved = norm_id in saved_set

            if is_shown or is_read or is_saved:
                if is_shown:
                    previously_shown_count += 1
                if is_read:
                    previously_read_count += 1
                if is_saved:
                    previously_saved_count += 1
                continue

            # 5. FILTER: CATEGORY-SPECIFIC VALIDATION
            if not self._passes_category_validation(
                candidate,
                context=context,
                reference_date=reference_date,
                max_age_years=max_age_years,
            ):
                category_filtered_count += 1
                continue

            filtered_candidates.append(candidate)

        logger.debug(
            "[candidate-filter] raw_result_count=%d metadata_invalid_count=%d "
            "english_filtered_count=%d duplicate_count=%d "
            "history_filtered_count=%d final_candidate_count=%d",
            original_count,
            invalid_count,
            language_filtered_count,
            duplicate_count,
            previously_shown_count + previously_read_count + previously_saved_count,
            len(filtered_candidates),
        )

        return CandidateFilterResult(
            original_count=original_count,
            duplicate_count=duplicate_count,
            invalid_count=invalid_count,
            language_filtered_count=language_filtered_count,
            previously_shown_count=previously_shown_count,
            previously_read_count=previously_read_count,
            previously_saved_count=previously_saved_count,
            category_filtered_count=category_filtered_count,
            final_count=len(filtered_candidates),
            candidates=filtered_candidates,
        )

    async def filter_for_user(
        self,
        session: AsyncSession,
        user_id: UUID,
        candidates: Sequence[SpotlightCandidate],
        *,
        context: SpotlightUserContext | None = None,
        reference_date: date | None = None,
        max_age_years: int = LATEST_RESEARCH_MAX_AGE_YEARS,
    ) -> CandidateFilterResult:
        """Fetch historical V2 spotlights for the user and apply all filters."""
        shown_ids, read_ids, saved_ids = await get_user_spotlight_history(
            session, user_id
        )
        if shown_ids:
            logger.info(
                "[learning_spotlight] Excluded previously recommended papers "
                "user_id=%s count=%s",
                user_id,
                len(shown_ids),
            )
        return self.filter_candidates(
            candidates,
            previously_shown_ids=shown_ids,
            already_read_ids=read_ids,
            already_saved_ids=saved_ids,
            context=context,
            reference_date=reference_date,
            max_age_years=max_age_years,
        )

    def _passes_category_validation(
        self,
        candidate: SpotlightCandidate,
        *,
        context: SpotlightUserContext | None,
        reference_date: date,
        max_age_years: int,
    ) -> bool:
        """Validate candidate against category-specific objective requirements."""
        st = candidate.spotlight_type

        if st == SpotlightType.influential_research:
            # Must have citation_count >= MIN_INFLUENTIAL_CITATIONS
            citation_count = candidate.citation_count or 0
            if citation_count < MIN_INFLUENTIAL_CITATIONS:
                return False
            return True

        if st == SpotlightType.latest_research:
            # Must be within recent 1–2 year window
            min_year = reference_date.year - max_age_years
            if candidate.year is None or candidate.year < min_year:
                return False
            return True

        if st == SpotlightType.beyond_your_field:
            # Deterministic check: if fields_of_study explicitly given and matches
            # solely the user's major/minor, exclude it.
            if context and (context.major or context.minor):
                user_fields = _user_major_minor_fields(context)
                fos_list = candidate.metadata.get("fields_of_study")
                if isinstance(fos_list, list) and fos_list:
                    candidate_fos = {
                        str(f).strip().lower()
                        for f in fos_list
                        if str(f).strip()
                    }
                    if candidate_fos and candidate_fos.issubset(user_fields):
                        return False
            return True

        if st == SpotlightType.country_perspective:
            # Keep candidate produced by strategy query. Country relevance is
            # query-approximated; metadata is preserved on candidate.
            return True

        if st == SpotlightType.leading_thinker:
            # Leading Thinker is not implemented; keep untouched if any candidate exists.
            return True

        return True


def _user_major_minor_fields(context: SpotlightUserContext) -> set[str]:
    """Original major/minor signals plus RapidFuzz-canonical forms.

    Derived structural query fragments are intentionally not treated as the
    user's major or minor.
    """
    fields: set[str] = set()
    for raw in (context.major, context.minor):
        if not raw or not str(raw).strip():
            continue
        cleaned = str(raw).strip()
        fields.add(cleaned.lower())
        canonical = canonicalize_signal(cleaned)
        if canonical:
            fields.add(canonical.strip().lower())
    return fields
