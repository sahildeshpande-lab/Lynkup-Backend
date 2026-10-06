"""Learning Spotlight – Dedicated Language Detection Service.

Uses the Lingua language detector to verify that candidate research papers
are written in English. Title must be English; when an abstract is present it
must also be English independently (combined-text detection is not used).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

try:
    from lingua import Language, LanguageDetector, LanguageDetectorBuilder

    _LINGUA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LINGUA_AVAILABLE = False

logger = logging.getLogger(__name__)

# Clip detection text so 30 abstracts cannot dominate a per-user timeout.
_MAX_DETECTION_CHARS = 400


@dataclass(frozen=True)
class LanguageDetectionResult:
    """Structured result from language detection."""

    is_english: bool
    detected_language: Optional[str] = None
    confidence: float = 0.0


def _clip_detection_text(text: str) -> str:
    if len(text) <= _MAX_DETECTION_CHARS:
        return text
    return text[:_MAX_DETECTION_CHARS]


class LanguageDetectionService:
    """Singleton-style language detector service backed by Lingua."""

    _detector: Optional[LanguageDetector] = None

    @classmethod
    def _get_detector(cls) -> Optional[LanguageDetector]:
        if not _LINGUA_AVAILABLE:
            logger.warning(
                "[language-detection] Lingua is not installed; language detection unavailable."
            )
            return None
        if cls._detector is None:
            try:
                # Spoken academic languages only. from_all_languages() is too slow
                # to build/run inside a per-user cron timeout.
                builder_languages = (
                    Language.ENGLISH,
                    Language.GERMAN,
                    Language.FRENCH,
                    Language.SPANISH,
                    Language.PORTUGUESE,
                    Language.ITALIAN,
                    Language.DUTCH,
                    Language.RUSSIAN,
                    Language.CHINESE,
                    Language.JAPANESE,
                )
                cls._detector = (
                    LanguageDetectorBuilder.from_languages(*builder_languages)
                    .with_low_accuracy_mode()
                    .build()
                )
            except Exception:
                logger.exception(
                    "[language-detection] Failed to initialize Lingua detector."
                )
                cls._detector = None
        return cls._detector

    @classmethod
    def preload(cls) -> None:
        """Build the detector once so the first user does not pay the cost."""
        cls._get_detector()

    @classmethod
    def detect_language(cls, text: str | None) -> LanguageDetectionResult:
        """Detect language of the given text safely without raising exceptions."""
        if not text or not text.strip():
            return LanguageDetectionResult(
                is_english=False, detected_language=None, confidence=0.0
            )

        cleaned_text = text.strip()
        try:
            detector = cls._get_detector()
            if detector is None:
                return LanguageDetectionResult(
                    is_english=False, detected_language="UNKNOWN", confidence=0.0
                )

            detected = detector.detect_language_of(cleaned_text)
            if detected == Language.ENGLISH:
                conf_values = detector.compute_language_confidence_values(cleaned_text)
                top_conf = conf_values[0].value if conf_values else 1.0
                return LanguageDetectionResult(
                    is_english=True,
                    detected_language="ENGLISH",
                    confidence=top_conf,
                )
            elif detected is not None:
                return LanguageDetectionResult(
                    is_english=False,
                    detected_language=detected.name,
                    confidence=0.0,
                )
            else:
                return LanguageDetectionResult(
                    is_english=False,
                    detected_language=None,
                    confidence=0.0,
                )
        except Exception:
            logger.exception(
                "[language-detection] Exception during language detection."
            )
            return LanguageDetectionResult(
                is_english=False,
                detected_language="ERROR",
                confidence=0.0,
            )

    @classmethod
    def _is_english_text(cls, text: str) -> bool:
        """Return True when Lingua classifies the given text as English."""
        cleaned = _clip_detection_text(text.strip())
        if not cleaned:
            return False
        try:
            detector = cls._get_detector()
            if detector is None:
                return False
            detected = detector.detect_language_of(cleaned)
            return bool(_LINGUA_AVAILABLE and detected == Language.ENGLISH)
        except Exception:
            logger.exception(
                "[language-detection] Exception during language detection."
            )
            return False

    @classmethod
    def is_english(cls, title: str | None, abstract: str | None = None) -> bool:
        """Evaluate whether a paper title (and abstract when present) is English.

        Acceptance Rules:
        - Title must be present and detected as English
        - When abstract is present, it must also be detected as English
        - English-only title with no abstract -> True
        - Non-English / unknown / empty title -> False
        - Detection error -> False
        """
        title_clean = (title or "").strip()
        abstract_clean = (abstract or "").strip()

        if not title_clean:
            return False
        if not cls._is_english_text(title_clean):
            return False
        if abstract_clean:
            return cls._is_english_text(abstract_clean)
        return True
