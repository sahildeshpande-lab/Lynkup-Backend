from apps.learningspotlight.schemas import get_spotlight_description

from .cycle_service import (
    CYCLE_LENGTH_DAYS,
    CYCLE_DAY_TO_SPOTLIGHT_TYPE,
    LearningSpotlightCycleService,
    get_cycle_day,
    get_spotlight_type,
)
from .daily_generation_service import (
    DailyGenerationResult,
    DefaultSpotlightPaperGenerator,
    LearningSpotlightDailyGenerationService,
    NotImplementedSpotlightPaperGenerator,
    SpotlightPaperGenerator,
    has_spotlight_for_cycle_day,
)
from .spotlight_strategy import SpotlightStrategy, get_spotlight_strategy
from .leading_thinker_strategy import (
    LeadingThinkerStrategy,
    calculate_author_influence_score,
    calculate_thinker_score,
    collect_unique_author_ids,
    select_authors_for_evaluation,
)
from .country_perspective_strategy import CountryPerspectiveStrategy
from .influential_research_strategy import (
    InfluentialResearchStrategy,
    MIN_INFLUENTIAL_CITATIONS,
)
from .latest_research_strategy import (
    LatestResearchStrategy,
    LATEST_RESEARCH_MAX_AGE_YEARS,
)
from .beyond_your_field_strategy import BeyondYourFieldStrategy
from .candidate_filter_service import (
    CandidateFilterService,
    get_user_spotlight_history,
)
from .candidate_scoring_service import CandidateScoringService
from .language_detection_service import (
    LanguageDetectionResult,
    LanguageDetectionService,
)
from .query_helpers import (
    build_country_perspective_query,
    build_semantic_scholar_query,
    build_spotlight_query,
    build_topic_search_condition,
    encode_spotlight_query,
    extract_country_perspective_concept,
    fields_of_study_from_user_interests,
    fields_of_study_from_user_profile,
    raw_papers_to_candidates,
    search_spotlight_candidates,
    select_country_perspective_concepts,
)
from .semantic_scholar_adapter import (
    SemanticScholarExternalError,
    get_authors_batch_v2,
    is_too_many_hits_error,
    search_papers_v2,
)
from .spotlight_persistence_service import SpotlightPersistenceService
from .admin_logs_service import list_learning_spotlight_logs
from .summarization_service import (
    PaperSummarizationService,
    generate_extractive_summary,
    split_into_sentences,
)
from .synthesis_service import (
    PaperSynthesisService,
    build_structured_synthesis_notes,
    compute_tfidf_cosine_similarity,
)

__all__ = [
    "CYCLE_LENGTH_DAYS",
    "CYCLE_DAY_TO_SPOTLIGHT_TYPE",
    "BeyondYourFieldStrategy",
    "CandidateFilterService",
    "CandidateScoringService",
    "CountryPerspectiveStrategy",
    "DailyGenerationResult",
    "DefaultSpotlightPaperGenerator",
    "InfluentialResearchStrategy",
    "LATEST_RESEARCH_MAX_AGE_YEARS",
    "LanguageDetectionResult",
    "LanguageDetectionService",
    "LeadingThinkerStrategy",
    "LatestResearchStrategy",
    "LearningSpotlightCycleService",
    "LearningSpotlightDailyGenerationService",
    "list_learning_spotlight_logs",
    "MIN_INFLUENTIAL_CITATIONS",
    "NotImplementedSpotlightPaperGenerator",
    "PaperSummarizationService",
    "PaperSynthesisService",
    "SpotlightPaperGenerator",
    "SpotlightPersistenceService",
    "SpotlightStrategy",
    "build_country_perspective_query",
    "build_semantic_scholar_query",
    "build_spotlight_query",
    "build_topic_search_condition",
    "encode_spotlight_query",
    "extract_country_perspective_concept",
    "fields_of_study_from_user_interests",
    "fields_of_study_from_user_profile",
    "build_structured_synthesis_notes",
    "calculate_author_influence_score",
    "calculate_thinker_score",
    "collect_unique_author_ids",
    "compute_tfidf_cosine_similarity",
    "generate_extractive_summary",
    "get_authors_batch_v2",
    "get_cycle_day",
    "get_spotlight_description",
    "get_spotlight_strategy",
    "get_spotlight_type",
    "get_user_spotlight_history",
    "has_spotlight_for_cycle_day",
    "is_too_many_hits_error",
    "raw_papers_to_candidates",
    "search_papers_v2",
    "search_spotlight_candidates",
    "select_authors_for_evaluation",
    "select_country_perspective_concepts",
    "SemanticScholarExternalError",
    "split_into_sentences",
]



