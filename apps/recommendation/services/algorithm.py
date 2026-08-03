from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
import re 


logger = logging.getLogger(__name__)

nlp = None
kw_model = None

_KEYBERT_MODEL = "all-MiniLM-L6-v2"
_SPACY_MODEL = "en_core_web_sm"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize_models() -> None:
    """Load the spaCy model once during application startup."""
    global nlp

    total_start = time.perf_counter()
    logger.info("[%s] Enter initialize_models()", _timestamp())

    if nlp is not None:
        logger.info("[%s] spaCy model already initialized; skipping duplicate startup load.", _timestamp())
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

    logger.info(
        "[%s] initialize_models() completed successfully (Total: %.2f sec). "
        "KeyBERT will load lazily on first keyword extraction request.",
        _timestamp(),
        time.perf_counter() - total_start,
    )


def get_keyword_model():
    """Return the cached KeyBERT model, loading it on first use."""
    global kw_model

    if kw_model is not None:
        logger.info("[%s] KeyBERT model already cached; reusing existing instance.", _timestamp())
        return kw_model

    load_start = time.perf_counter()
    logger.info("[%s] Loading KeyBERT model for the first time...", _timestamp())
    try:
        from keybert import KeyBERT

        logger.info("[%s] Beginning KeyBERT initialization for %s", _timestamp(), _KEYBERT_MODEL)
        kw_model = KeyBERT(model=_KEYBERT_MODEL)
        logger.info(
            "[%s] KeyBERT model loaded successfully (%.2f sec).",
            _timestamp(),
            time.perf_counter() - load_start,
        )
    except Exception:
        logger.exception("[%s] Failed while loading KeyBERT", _timestamp())
        raise

    return kw_model


def _ensure_initialized() -> None:
    if nlp is None:
        try:
            initialize_models()
        except Exception as exc:
            raise RuntimeError(
                "Recommendation models are not initialized. "
                "Install spaCy and the configured language model before using "
                "keyword extraction."
            ) from exc


def extract_hashtags(text: str) -> list[str]:
    from apps.feed.content_utils import extract_hashtags as extract_content_hashtags

    return extract_content_hashtags(caption=text, content_html=None)


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

    model = get_keyword_model()
    raw_keywords = model.extract_keywords(
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
