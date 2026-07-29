from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

nlp = None
kw_model = None


def initialize_models() -> None:
    """Load spaCy and KeyBERT models once during application startup."""
    global nlp, kw_model

    logger.info("Entered initialize_models()")

    if nlp is not None and kw_model is not None:
        return

    try:
        logger.info("Loading spaCy model...")
        import spacy

        nlp = spacy.load("en_core_web_sm")
        logger.info("spaCy model loaded successfully.")
    except Exception:
        logger.exception("Failed to load spaCy model during recommendation startup.")
        raise

    try:
        logger.info("Loading KeyBERT model...")
        from keybert import KeyBERT

        kw_model = KeyBERT(model="all-MiniLM-L6-v2")
        logger.info("KeyBERT model loaded successfully.")
    except Exception:
        logger.exception("Failed to load KeyBERT model during recommendation startup.")
        raise


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
