from __future__ import annotations

import logging
import re
from typing import Any

from apps.recommendations.services.model_registry import get_registry

logger = logging.getLogger(__name__)

# Backward-compatible module aliases populated after startup initialization.
nlp: Any | None = None
kw_model: Any | None = None


def initialize_models() -> None:
    """Load spaCy and KeyBERT once during application startup.

    Thread-safe and idempotent. Each worker process loads its own model instances.
    """
    global nlp, kw_model

    registry = get_registry()
    registry.initialize()
    nlp = registry.nlp
    kw_model = registry.kw_model


def get_keyword_model() -> Any:
    """Return the cached KeyBERT model loaded during startup."""
    registry = get_registry()
    if registry.is_ready:
        return registry.kw_model
    raise RuntimeError(
        "KeyBERT model is not initialized. "
        "Ensure initialize_models() runs during application startup."
    )


def _ensure_initialized() -> None:
    if not get_registry().is_ready:
        raise RuntimeError(
            "Recommendation models are not initialized. "
            "Install spaCy/KeyBERT dependencies and ensure initialize_models() "
            "runs during application startup."
        )


def extract_hashtags(text: str) -> list[str]:
    from apps.feed.content_utils import extract_hashtags as extract_content_hashtags

    return extract_content_hashtags(caption=text, content_html=None)


def remove_hashtags(text: str) -> str:
    """Remove hashtags before preprocessing."""
    return re.sub(r"#\w+", "", text)


def preprocess_text(text: str) -> str:
    """Remove stop words, punctuation, numbers and lemmatize."""
    _ensure_initialized()

    doc = get_registry().nlp(text)

    tokens = [
        token.lemma_.lower()
        for token in doc
        if not token.is_stop
        and not token.is_punct
        and not token.like_num
        and token.is_alpha
    ]

    return " ".join(tokens)


def extract_keywords(processed_text: str) -> list[tuple[str, float]]:
    """
    Extract keywords using KeyBERT, then apply post-processing cleanup.

    Returns cleaned (keyword, score) tuples sorted by score descending.
    """
    from apps.recommendations.services.keyword_postprocessing import clean_keywords

    _ensure_initialized()

    model = get_keyword_model()
    raw_keywords = model.extract_keywords(
        processed_text,
        keyphrase_ngram_range=(1, 3),
        top_n=10,
        stop_words=None,  # Already removed by spaCy
        use_maxsum=True,
        nr_candidates=20,
    )

    return clean_keywords(list(raw_keywords))


def extract_post_keywords(text: str) -> dict:
    """
    Complete pipeline:
    1. Extract hashtags
    2. Remove hashtags
    3. Preprocess text
    4. Extract keywords using KeyBERT
    5. Post-process keyword candidates
    """

    hashtags = extract_hashtags(text)

    cleaned_text = remove_hashtags(text)

    processed_text = preprocess_text(cleaned_text)
    logger.info(
        "[post-keyword-extraction]\nProcessed Text\n%s",
        processed_text,
    )
    logger.info(
        "[post-keyword-extraction]\nExtracted Hashtags\n%s",
        hashtags,
    )

    keywords_with_scores = extract_keywords(processed_text)
    keywords = [keyword for keyword, _ in keywords_with_scores]
    logger.info(
        "[post-keyword-extraction]\nExtracted Keywords\n%s",
        keywords,
    )

    return {
        "processed_text": processed_text,
        "hashtags": hashtags,
        "keywords": keywords,
        "keywords_with_scores": keywords_with_scores,
    }


if __name__ == "__main__":
    initialize_models()
