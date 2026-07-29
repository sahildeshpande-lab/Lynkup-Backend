from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

nlp = None
kw_model = None

_SENTENCE_TRANSFORMER_MODEL = "all-MiniLM-L6-v2"
_SPACY_MODEL = "en_core_web_sm"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_huggingface_cache_paths() -> None:
    import os

    logger.info("[%s] HuggingFace cache environment:", _timestamp())
    for env_var in (
        "HF_HOME",
        "TRANSFORMERS_CACHE",
        "SENTENCE_TRANSFORMERS_HOME",
        "HUGGINGFACE_HUB_CACHE",
    ):
        logger.info("[%s]   %s=%r", _timestamp(), env_var, os.environ.get(env_var))

    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        logger.info("[%s] HuggingFace hub cache path=%s", _timestamp(), HF_HUB_CACHE)
    except Exception:
        logger.exception("[%s] Failed while resolving HuggingFace hub cache path", _timestamp())


def initialize_models() -> None:
    """Load spaCy and KeyBERT models once during application startup."""
    global nlp, kw_model

    total_start = time.perf_counter()
    logger.info("[%s] Enter initialize_models()", _timestamp())

    if nlp is not None and kw_model is not None:
        logger.info("[%s] Models already initialized; skipping duplicate startup load.", _timestamp())
        return

    spacy_start = time.perf_counter()
    logger.info("[%s] Loading spaCy model...", _timestamp())
    try:
        import spacy

        logger.info("[%s] spaCy package imported.", _timestamp())
        logger.info("[%s] Beginning spaCy model load for %s", _timestamp(), _SPACY_MODEL)
        nlp = spacy.load(_SPACY_MODEL)
        logger.info(
            "[%s] spaCy model loaded successfully (%.2f sec)",
            _timestamp(),
            time.perf_counter() - spacy_start,
        )
    except Exception:
        logger.exception("[%s] Failed while loading spaCy", _timestamp())
        raise

    _log_huggingface_cache_paths()

    sentence_transformer_start = time.perf_counter()
    logger.info("[%s] Loading SentenceTransformer...", _timestamp())
    try:
        from sentence_transformers import SentenceTransformer

        logger.info(
            "[%s] Beginning SentenceTransformer download/load for %s",
            _timestamp(),
            _SENTENCE_TRANSFORMER_MODEL,
        )
        sentence_transformer_model = SentenceTransformer(_SENTENCE_TRANSFORMER_MODEL)
        logger.info(
            "[%s] SentenceTransformer download/load completed.",
            _timestamp(),
        )
        logger.info("[%s] SentenceTransformer model moved into memory.", _timestamp())
        logger.info(
            "[%s] SentenceTransformer loaded successfully (%.2f sec)",
            _timestamp(),
            time.perf_counter() - sentence_transformer_start,
        )
    except Exception:
        logger.exception("[%s] Failed while loading SentenceTransformer", _timestamp())
        raise

    keybert_start = time.perf_counter()
    logger.info("[%s] Initializing KeyBERT...", _timestamp())
    try:
        from keybert import KeyBERT

        logger.info("[%s] Beginning KeyBERT initialization.", _timestamp())
        kw_model = KeyBERT(model=sentence_transformer_model)
        logger.info("[%s] KeyBERT model moved into memory.", _timestamp())
        logger.info(
            "[%s] KeyBERT initialized successfully (%.2f sec)",
            _timestamp(),
            time.perf_counter() - keybert_start,
        )
    except Exception:
        logger.exception("[%s] Failed while loading KeyBERT", _timestamp())
        raise

    logger.info(
        "[%s] initialize_models() completed successfully (Total: %.2f sec)",
        _timestamp(),
        time.perf_counter() - total_start,
    )


def _ensure_initialized() -> None:
    if nlp is None or kw_model is None:
        raise RuntimeError(
            "Recommendation models are not initialized. "
            "Call initialize_models() during application startup before using "
            "keyword extraction."
        )


def extract_hashtags(text: str) -> list[str]:
    hashtags = re.findall(r"#([A-Za-z0-9_]+)", text)

    # Remove duplicates while preserving order
    return list(dict.fromkeys(hashtags))


def remove_hashtags(text: str) -> str:
    """
    Remove hashtags before preprocessing.
    """
    return re.sub(r"#\w+", "", text)


def preprocess_text(text: str) -> str:
    """
    Remove stop words, punctuation, numbers and lemmatize.
    """
    _ensure_initialized()

    doc = nlp(text)

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
    from apps.recommendation.services.keyword_postprocessing import clean_keywords

    _ensure_initialized()

    raw_keywords = kw_model.extract_keywords(
        processed_text,
        keyphrase_ngram_range=(1, 3),
        top_n=10,
        stop_words=None,      # Already removed by spaCy
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

    keywords_with_scores = extract_keywords(processed_text)

    return {
        "processed_text": processed_text,
        "hashtags": hashtags,
        "keywords": [keyword for keyword, _ in keywords_with_scores],
        "keywords_with_scores": keywords_with_scores,
    }


if __name__ == "__main__":

    initialize_models()

    text = """
    I am currently learning FastAPI, Python, Machine Learning,
    Natural Language Processing and Large Language Models.
    I love building AI applications with Semantic Search.
    #Python #MachineLearning #FastAPI #AI
    """

    result = extract_post_keywords(text)

    print("Processed Text:")
    print(result["processed_text"])

    print("\nHashtags:")
    print(result["hashtags"])

    print("\nKeywords:")
    print(result["keywords"])
