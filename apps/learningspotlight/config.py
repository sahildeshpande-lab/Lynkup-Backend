"""Environment configuration for Learning Spotlight (V2).

Cycle start date is persisted on ``learning_recommendation_settings.cycle_start_date``
(not via env). This settings object is reserved for future Spotlight-only env knobs.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LearningSpotlightSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    learning_spotlight_candidate_limit: int = Field(
        default=50,
        alias="LEARNING_SPOTLIGHT_CANDIDATE_LIMIT",
        gt=0,
        description="Candidate papers to fetch from Semantic Scholar before filtering.",
    )
    learning_spotlight_fetch_escalation_attempts: int = Field(
        default=3,
        alias="LEARNING_SPOTLIGHT_FETCH_ESCALATION_ATTEMPTS",
        ge=1,
        description=(
            "Per-user fetch retries with increasing Semantic Scholar limits "
            "until enough papers survive filtering for learning_spotlight_papers_count."
        ),
    )

    learning_spotlight_batch_size: int = Field(
        default=50,
        alias="LEARNING_SPOTLIGHT_BATCH_SIZE",
        gt=0,
        description="Batch size of eligible users to process per iteration in daily cron.",
    )

    # Step 8 Scoring weights (must sum to 1.0)
    weight_user_relevance: float = Field(
        default=0.40,
        alias="LEARNING_SPOTLIGHT_WEIGHT_USER_RELEVANCE",
        ge=0.0,
        le=1.0,
    )
    weight_paper_quality: float = Field(
        default=0.20,
        alias="LEARNING_SPOTLIGHT_WEIGHT_PAPER_QUALITY",
        ge=0.0,
        le=1.0,
    )
    weight_recency: float = Field(
        default=0.10,
        alias="LEARNING_SPOTLIGHT_WEIGHT_RECENCY",
        ge=0.0,
        le=1.0,
    )
    weight_category: float = Field(
        default=0.30,
        alias="LEARNING_SPOTLIGHT_WEIGHT_CATEGORY",
        ge=0.0,
        le=1.0,
    )

    # Step 11 Leading Thinker component weights
    weight_thinker_author_influence: float = Field(
        default=0.70,
        alias="LEARNING_SPOTLIGHT_WEIGHT_THINKER_AUTHOR",
        ge=0.0,
        le=1.0,
    )
    weight_thinker_paper_relevance: float = Field(
        default=0.30,
        alias="LEARNING_SPOTLIGHT_WEIGHT_THINKER_RELEVANCE",
        ge=0.0,
        le=1.0,
    )

    learning_spotlight_max_query_terms: int = Field(
        default=8,
        alias="LEARNING_SPOTLIGHT_MAX_QUERY_TERMS",
        gt=0,
        description="Maximum OR-terms in a Semantic Scholar boolean query.",
    )
    learning_spotlight_country_max_concepts: int = Field(
        default=8,
        alias="LEARNING_SPOTLIGHT_COUNTRY_MAX_CONCEPTS",
        gt=0,
        description=(
            "Maximum Country Perspective concepts (major/minor/interests) "
            "included in one Semantic Scholar OR query."
        ),
    )
    learning_spotlight_query_narrow_max_attempts: int = Field(
        default=2,
        alias="LEARNING_SPOTLIGHT_QUERY_NARROW_MAX_ATTEMPTS",
        ge=0,
        description="Extra attempts that drop overlapping/broad terms after a too-many-hits error.",
    )
    learning_spotlight_max_authors_evaluated: int = Field(
        default=15,
        alias="LEARNING_SPOTLIGHT_MAX_AUTHORS_EVALUATED",
        gt=0,
        description="Hard cap on authors sent to Semantic Scholar /author/batch.",
    )
    learning_spotlight_user_timeout_seconds: float = Field(
        default=180.0,
        alias="LEARNING_SPOTLIGHT_USER_TIMEOUT_SECONDS",
        gt=0,
        description=(
            "Legacy env compatibility. Per-user generation is no longer cancelled; "
            "HTTP timeouts live on Semantic Scholar requests."
        ),
    )

    # Kept for env compatibility; the worker runs daily at 00:00 UTC.
    learning_spotlight_cron_interval_hours: int = Field(
        default=24,
        alias="LEARNING_SPOTLIGHT_CRON_INTERVAL_HOURS",
        gt=0,
        description="Legacy interval hours. Celery Beat runs daily at 00:00 UTC.",
    )


settings = LearningSpotlightSettings()
