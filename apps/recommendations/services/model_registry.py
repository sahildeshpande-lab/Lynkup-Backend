from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any

from apps.recommendations.config import settings as recommendation_settings

if TYPE_CHECKING:
    from keybert import KeyBERT

logger = logging.getLogger(__name__)

_KEYBERT_MODEL = "all-MiniLM-L6-v2"
_SPACY_MODEL = "en_core_web_sm"


def _configure_huggingface_cache() -> None:
    """Ensure Hugging Face / SentenceTransformer caches use HF_HOME when set."""
    hf_home = (
        recommendation_settings.hf_home
        or os.getenv("HF_HOME")
        or "/tmp/huggingface"
    ).strip()
    if not hf_home:
        return

    os.environ.setdefault("HF_HOME", hf_home)
    os.environ.setdefault("TRANSFORMERS_CACHE", hf_home)
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", hf_home)
    logger.info("Using Hugging Face cache directory: %s", hf_home)


class RecommendationModelRegistry:
    """Thread-safe singleton registry for spaCy and KeyBERT models.

    Each Uvicorn/Gunicorn worker process holds one registry instance. Models are
    loaded exactly once per worker during application startup.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nlp: Any | None = None
        self._kw_model: KeyBERT | None = None
        self._initialized = False

    @property
    def is_ready(self) -> bool:
        """Return True when both spaCy and KeyBERT models are loaded."""
        return self._initialized and self._nlp is not None and self._kw_model is not None

    @property
    def nlp(self) -> Any:
        """Return the loaded spaCy language model."""
        if self._nlp is None:
            raise RuntimeError(
                "spaCy model is not initialized. "
                "Call initialize_models() during application startup."
            )
        return self._nlp

    @property
    def kw_model(self) -> KeyBERT:
        """Return the loaded KeyBERT model."""
        if self._kw_model is None:
            raise RuntimeError(
                "KeyBERT model is not initialized. "
                "Call initialize_models() during application startup."
            )
        return self._kw_model

    def initialize(self) -> None:
        """Load spaCy and KeyBERT exactly once. Fail fast on any error."""
        if self.is_ready:
            logger.info("Recommendation models already initialized; reusing cached instances.")
            return

        with self._lock:
            if self.is_ready:
                logger.info("Recommendation models already initialized; reusing cached instances.")
                return

            total_start = time.perf_counter()
            self._load_spacy()
            self._load_keybert()
            self._initialized = True
            logger.info(
                "Recommendation models initialized successfully in %.2f seconds.",
                time.perf_counter() - total_start,
            )

    def _load_spacy(self) -> None:
        load_start = time.perf_counter()
        logger.info("Loading spaCy model %s...", _SPACY_MODEL)
        try:
            import spacy

            self._nlp = spacy.load(_SPACY_MODEL)
        except Exception:
            logger.exception("Failed to load spaCy model %s", _SPACY_MODEL)
            raise

        logger.info(
            "spaCy model loaded successfully in %.2f seconds.",
            time.perf_counter() - load_start,
        )

    def _load_keybert(self) -> None:
        load_start = time.perf_counter()
        logger.info("Loading KeyBERT model...")
        _configure_huggingface_cache()
        try:
            from keybert import KeyBERT

            logger.info("Beginning KeyBERT initialization for %s", _KEYBERT_MODEL)
            self._kw_model = KeyBERT(model=_KEYBERT_MODEL)
        except Exception:
            logger.exception("Failed to load KeyBERT model %s", _KEYBERT_MODEL)
            raise

        logger.info(
            "KeyBERT model loaded successfully in %.2f seconds.",
            time.perf_counter() - load_start,
        )


_registry = RecommendationModelRegistry()


def get_registry() -> RecommendationModelRegistry:
    """Return the process-wide recommendation model registry."""
    return _registry
