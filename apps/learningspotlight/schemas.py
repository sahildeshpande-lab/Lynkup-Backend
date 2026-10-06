from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from common.enums import SpotlightFeedback, SpotlightType

SPOTLIGHT_DESCRIPTION_TITLE = "Learning Spotlight"

SPOTLIGHT_TYPE_TO_SUBTITLE: dict[SpotlightType, str] = {
    SpotlightType.leading_thinker: "Articles matched to your major, minor, and academic interests",
    SpotlightType.country_perspective: "Articles based on your location of study",
    SpotlightType.influential_research: "Articles trending in your field",
    SpotlightType.latest_research: "Articles based on the latest research in your field",
    SpotlightType.beyond_your_field: "Articles outside of your field to broaden your knowledge",
}


def get_spotlight_description(spotlight_type: SpotlightType | str) -> dict[str, str]:
    """Return the API title/subtitle for the stored Learning Spotlight type.

    Subtitle follows ``spotlight_type`` so a remapped cycle still shows the copy
    that matches the papers that were generated.
    """
    if isinstance(spotlight_type, SpotlightType):
        resolved = spotlight_type
    else:
        try:
            resolved = SpotlightType(spotlight_type)
        except ValueError as exc:
            raise ValueError(f"Unknown spotlight type: {spotlight_type}") from exc
    subtitle = SPOTLIGHT_TYPE_TO_SUBTITLE.get(resolved)
    if subtitle is None:
        raise ValueError(f"Unknown spotlight type: {resolved}")
    return {
        "title": SPOTLIGHT_DESCRIPTION_TITLE,
        "subtitle": subtitle,
    }


def format_recommended_cycle_name(spotlight_type: SpotlightType | str | None) -> str | None:
    """Human-readable cycle name from a stored ``spotlight_type`` value.

    Example: ``leading_thinker`` -> ``Leading Thinker``.
    """
    if spotlight_type is None:
        return None
    if isinstance(spotlight_type, SpotlightType):
        value = spotlight_type.value
    else:
        value = str(spotlight_type).strip()
        if not value:
            return None
    return value.replace("_", " ").title()


def extract_recommended_cycle_name(spotlight: Any) -> str | None:
    """Derive recommended cycle name from a ``learning_spotlight`` snapshot dict."""
    if not isinstance(spotlight, dict):
        return None
    return format_recommended_cycle_name(spotlight.get("spotlight_type"))


def extract_recommended_papers(spotlight: Any) -> tuple[list[str], list[str]]:
    """Return ordered ``(paper_ids, paper_titles)`` from a spotlight snapshot.

    Supports multi-paper ``papers`` lists and the legacy single ``paper`` field.
    Entries missing an id are skipped; missing titles become empty strings so
    the two lists stay aligned.
    """
    paper_ids: list[str] = []
    paper_titles: list[str] = []
    if not isinstance(spotlight, dict):
        return paper_ids, paper_titles

    papers: list[Any] = []
    raw_papers = spotlight.get("papers")
    if isinstance(raw_papers, list) and raw_papers:
        papers.extend(raw_papers)
    elif isinstance(spotlight.get("paper"), dict):
        papers.append(spotlight["paper"])

    seen: set[str] = set()
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        raw_id = paper.get("paper_id")
        if raw_id is None:
            continue
        paper_id = str(raw_id).strip()
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        title = paper.get("title")
        paper_ids.append(paper_id)
        paper_titles.append(title.strip() if isinstance(title, str) else "")

    return paper_ids, paper_titles


# Re-export for Learning Spotlight consumers.
__all__ = [
    "CandidateFilterResult",
    "CycleConfiguration",
    "CycleDayInfo",
    "get_spotlight_description",
    "format_recommended_cycle_name",
    "extract_recommended_cycle_name",
    "extract_recommended_papers",
    "LearningSpotlight",
    "LearningSpotlightAdminUserItem",
    "LearningSpotlightAuthor",
    "LearningSpotlightEngagement",
    "LearningSpotlightPaper",
    "ScoredSpotlightCandidate",
    "SpotlightCandidate",
    "SpotlightDescription",
    "SpotlightFeedback",
    "SpotlightFeedbackRequest",
    "SpotlightRankingResult",
    "SpotlightReadRequest",
    "SpotlightSaveRequest",
    "SpotlightType",
    "SpotlightUserContext",
    "SpotlightAdminLogItem",
    "SpotlightAdminLogLikeSummary",
    "SpotlightAdminLogSummary",
]


class CycleDayInfo(BaseModel):
    """Placeholder response shape for cycle introspection (not wired to routes yet)."""

    cycle_day: int = Field(..., ge=1, le=5, description="Day in the global 5-day cycle (1-5)")
    spotlight_type: SpotlightType


class SpotlightDescription(BaseModel):
    """User-facing title and type-based subtitle for the Learning Spotlight API."""

    title: str
    subtitle: str


class LearningSpotlightAuthor(BaseModel):
    """Author entry stored on a Learning Spotlight paper."""

    author_id: str | None = None
    name: str | None = None


class LearningSpotlightEngagement(BaseModel):
    """Per-user engagement flags for an individual spotlight paper."""

    is_read: bool = Field(default=False, strict=True)
    is_saved: bool = Field(default=False, strict=True)
    feedback: bool = Field(default=False)

    @field_validator("feedback", mode="before")
    @classmethod
    def coerce_feedback(cls, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            val_lower = value.strip().lower()
            if val_lower in ("true", "1", "useful", "liked", "like", "not_useful", "disliked", "dislike"):
                return True
            if val_lower in ("false", "0", "none", "null", ""):
                return False
        return bool(value)




class LearningSpotlightPaper(BaseModel):
    """Selected paper metadata with individual score and engagement."""

    paper_id: str
    title: str | None = None
    authors: list[LearningSpotlightAuthor] = Field(default_factory=list)
    abstract: str | None = None
    venue: str | None = None
    year: int | None = None
    citation_count: int | None = None
    url: str | None = None
    score: float | None = None
    engagement: LearningSpotlightEngagement = Field(
        default_factory=LearningSpotlightEngagement
    )


class LearningSpotlight(BaseModel):
    """Current Learning Spotlight snapshot stored on ``profiles.learning_spotlight``.

    Shape stores multiple papers (default count configured by admin).
    Backward-compatible with legacy single-paper snapshots.
    """

    version: int = Field(default=2, ge=2, le=2)
    cycle_day: int = Field(..., ge=1, le=5)
    spotlight_type: SpotlightType
    query: str
    papers: list[LearningSpotlightPaper] = Field(default_factory=list)
    generated_at: datetime

    @computed_field
    @property
    def description(self) -> SpotlightDescription:
        """API-only title/subtitle derived from ``spotlight_type``, not persisted."""
        return SpotlightDescription.model_validate(
            get_spotlight_description(self.spotlight_type)
        )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_single_paper(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # If "papers" is missing or empty, but legacy "paper" is present
            if not data.get("papers") and data.get("paper"):
                raw_paper = data["paper"]
                legacy_paper = (
                    dict(raw_paper)
                    if isinstance(raw_paper, dict)
                    else (
                        raw_paper.model_dump(mode="python")
                        if hasattr(raw_paper, "model_dump")
                        else dict(raw_paper)
                    )
                )
                legacy_eng = data.get("engagement")
                if legacy_eng is not None:
                    if isinstance(legacy_eng, dict):
                        legacy_paper["engagement"] = legacy_eng
                    elif hasattr(legacy_eng, "model_dump"):
                        legacy_paper["engagement"] = legacy_eng.model_dump(mode="python")
                    else:
                        legacy_paper["engagement"] = legacy_eng
                if "score" in data:
                    legacy_paper["score"] = data["score"]
                data = dict(data)
                data["papers"] = [legacy_paper]
        return data

    @property
    def paper(self) -> LearningSpotlightPaper | None:
        """Backward-compatible helper returning the primary (first) paper."""
        return self.papers[0] if self.papers else None

    @property
    def engagement(self) -> LearningSpotlightEngagement:
        """Backward-compatible helper returning the primary paper's engagement."""
        return self.papers[0].engagement if self.papers else LearningSpotlightEngagement()

    @property
    def score(self) -> float | None:
        """Backward-compatible helper returning the primary paper's score."""
        return self.papers[0].score if self.papers else None


class SpotlightUserContext(BaseModel):
    """Service-layer DTO of learning signals for a strategy (not the Profile ORM)."""

    user_id: UUID
    country: str | None = None
    major: str | None = None
    minor: str | None = None
    interests: list[str] = Field(default_factory=list)
    extracted_keywords: dict[str, Any] = Field(default_factory=dict)
    search_limit: int | None = Field(
        default=None,
        description="Optional Semantic Scholar page size override for this fetch.",
    )

    @field_validator("interests", mode="before")
    @classmethod
    def coerce_interests(cls, value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, (list, tuple, set)):
            return [
                str(item).strip()
                for item in value
                if item is not None and str(item).strip()
            ]
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        return [str(value)]



class SpotlightCandidate(BaseModel):
    """Intermediate paper candidate before filter → score → rank → selection.

    Not the persisted ``LearningSpotlight`` JSON. ``query`` is the SS query that
    produced this candidate; ``metadata`` holds optional strategy-specific fields.
    """

    paper_id: str
    title: str | None = None
    authors: list[LearningSpotlightAuthor] = Field(default_factory=list)
    abstract: str | None = None
    venue: str | None = None
    year: int | None = None
    citation_count: int | None = None
    url: str | None = None
    query: str
    spotlight_type: SpotlightType
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateFilterResult(BaseModel):
    """Structured result from the Learning Spotlight candidate filtering pipeline."""

    original_count: int = 0
    duplicate_count: int = 0
    invalid_count: int = 0
    language_filtered_count: int = 0
    previously_shown_count: int = 0
    previously_read_count: int = 0
    previously_saved_count: int = 0
    category_filtered_count: int = 0
    final_count: int = 0
    candidates: list[SpotlightCandidate] = Field(default_factory=list)



class ScoredSpotlightCandidate(BaseModel):
    """A Spotlight candidate enriched with component scores and normalized final score."""

    paper_id: str
    title: str | None = None
    authors: list[LearningSpotlightAuthor] = Field(default_factory=list)
    abstract: str | None = None
    venue: str | None = None
    year: int | None = None
    citation_count: int | None = None
    url: str | None = None
    query: str
    spotlight_type: SpotlightType
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Component scores (all 0-100)
    user_relevance_score: float = Field(..., ge=0.0, le=100.0)
    quality_score: float = Field(..., ge=0.0, le=100.0)
    recency_score: float = Field(..., ge=0.0, le=100.0)
    category_score: float = Field(..., ge=0.0, le=100.0)
    final_score: float = Field(..., ge=0.0, le=100.0)


class SpotlightRankingResult(BaseModel):
    """Result of scoring and ranking candidates, including the selected #1 paper."""

    ranked_candidates: list[ScoredSpotlightCandidate] = Field(default_factory=list)
    selected_candidate: ScoredSpotlightCandidate | None = None
    total_candidates: int = 0


class SpotlightReadRequest(BaseModel):
    """Request payload for marking a Learning Spotlight paper as read."""

    is_read: bool | None = None
    paper_id: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None


class SpotlightSaveRequest(BaseModel):
    """Request payload for saving/unsaving a Learning Spotlight paper."""

    is_saved: bool = Field(..., strict=True)
    paper_id: str | None = None


class SpotlightFeedbackRequest(BaseModel):
    """Request payload for rating a Learning Spotlight paper."""

    feedback: SpotlightFeedback | bool = Field(default=True)
    paper_id: str | None = None





class PaperSummarizeRequest(BaseModel):
    """Optional payload when requesting an extractive summary for a paper."""

    title: str | None = None
    abstract: str | None = None


class PaperSynthesizeRequest(BaseModel):
    """Optional payload when requesting multi-paper synthesis."""

    title: str | None = None
    abstract: str | None = None


class SynthesisArticleNotes(BaseModel):
    title: str
    key_points: list[str] = Field(default_factory=list)


class SynthesisRelatedArticleNotes(SynthesisArticleNotes):
    label: str


class SynthesisContent(BaseModel):
    """Structured Article Synthesis payload shown on the Learning Spotlight screen."""

    heading: str = "Article Synthesis"
    common_themes_from_related_articles: list[str] = Field(default_factory=list)
    original_article: SynthesisArticleNotes
    related_articles: list[SynthesisRelatedArticleNotes] = Field(default_factory=list)
    key_differences: list[str] = Field(
        default_factory=list,
        description="Extractive claims that distinguish the original article from related work.",
    )
    key_similarities: list[str] = Field(
        default_factory=list,
        description="Extractive overlapping claims shared by the original article and related work.",
    )
    overall_takeaway: str = Field(
        default="",
        description="Comprehensive summary synthesizing the content of the entire document.",
    )


class SavedPaperItem(BaseModel):
    """Structured response item for a paper saved by the user."""

    paper_id: str
    title: str | None = None
    authors: list[LearningSpotlightAuthor] = Field(default_factory=list)
    abstract: str | None = None
    venue: str | None = None
    year: int | None = None
    citation_count: int | None = None
    url: str | None = None
    saved_at: datetime | None = None


class CycleConfiguration(BaseModel):
    """Admin configuration defining the rotation order of the 5 spotlight categories."""

    cycle: list[SpotlightType] = Field(
        ...,
        description="Ordered list of exactly 5 unique SpotlightType values.",
    )

    @field_validator("cycle")
    @classmethod
    def validate_cycle(cls, v: list[SpotlightType]) -> list[SpotlightType]:
        if len(v) != 5:
            raise ValueError("cycle must contain exactly 5 spotlight types.")
        if len(set(v)) != 5:
            raise ValueError("cycle must not contain duplicate spotlight types.")
        required_types = set(SpotlightType)
        if set(v) != required_types:
            raise ValueError(f"cycle must contain all supported spotlight types: {required_types}")
        return v


class SpotlightAdminLogLikeSummary(BaseModel):
    """Tri-state counts for grouped like feedback on admin log records."""

    true: int = 0
    false: int = 0
    null: int = 0


class SpotlightAdminLogSummary(BaseModel):
    """Counts of grouped Learning Spotlight admin log records by derived state."""

    is_saved: int = 0
    is_summarizes: int = 0
    is_sythesis: int = 0
    is_read: int = 0
    is_like: SpotlightAdminLogLikeSummary = Field(default_factory=SpotlightAdminLogLikeSummary)
    is_skip: int = 0


class SpotlightAdminLogItem(BaseModel):
    """One grouped user/paper record derived from Learning Spotlight action logs."""

    user_id: UUID
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    university: str | None = None
    university_details: dict[str, Any] | None = None
    major: str | None = None
    major_details: dict[str, Any] | None = None
    minor: str | None = None
    minor_details: dict[str, Any] | None = None
    country: str | None = None
    country_details: dict[str, Any] | None = None
    educationLevel: str | None = None
    educationLevel_details: dict[str, Any] | None = None
    paper_title: str
    paper_id: str
    is_saved: bool = False
    is_summarizes: bool = False
    is_sythesis: bool = False
    is_read: bool = False
    is_like: bool | None = None
    is_skip: bool = False
    read_time_seconds: int = 0
    created_at: datetime
    recommended_cycle_name: str | None = None


class LearningSpotlightAdminUserItem(BaseModel):
    """Admin user list item: full user payload plus Learning Spotlight fields."""

    model_config = ConfigDict(extra="allow")

    user_id: str
    extracted_keywords: dict[str, Any] | list[Any] | None = None
    is_learning_spotlight_recommended: bool
    learning_spotlight_recommended_at: datetime | str | None = None
    recommended_cycle_name: str | None = None
    paper_id: list[str] = Field(default_factory=list)
    paper_title: list[str] = Field(default_factory=list)
    firstName: str | None = None



