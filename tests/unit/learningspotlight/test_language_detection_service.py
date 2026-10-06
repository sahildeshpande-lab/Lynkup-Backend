"""Unit tests for Learning Spotlight Language Detection Service.

Tests:
1. English title + English abstract -> accepted (is_english is True).
2. English title + no abstract -> accepted (is_english is True).
3. Non-English text (German, French, Spanish, etc.) -> rejected (is_english is False).
4. Empty title + empty abstract -> rejected safely (is_english is False).
5. Missing abstract does not crash and processes title.
6. Ambiguous / empty / whitespace text -> rejected safely.
7. Detector exception handled gracefully without crashing caller.
8. Language filter is executed in CandidateFilterService before duplicates/category filters.
9. Language filtering increments language_filtered_count in CandidateFilterResult.
10. Fallback behavior when Lingua is unavailable or returns None.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from lingua import Language

from apps.learningspotlight.schemas import (
    CandidateFilterResult,
    LearningSpotlightAuthor,
    SpotlightCandidate,
    SpotlightUserContext,
)
from apps.learningspotlight.services.candidate_filter_service import (
    CandidateFilterService,
)
from apps.learningspotlight.services.language_detection_service import (
    LanguageDetectionResult,
    LanguageDetectionService,
)
from common.enums import SpotlightType


# ---------------------------------------------------------------------------
# 1-6. Direct Service Tests
# ---------------------------------------------------------------------------


def test_english_title_and_abstract_accepted() -> None:
    """1. Academic English title and abstract are detected as English."""
    title = "Attention Is All You Need"
    abstract = (
        "The dominant sequence transduction models are based on complex "
        "recurrent or convolutional neural networks that include an encoder and a decoder."
    )
    assert LanguageDetectionService.is_english(title, abstract) is True

    result = LanguageDetectionService.detect_language(f"{title}. {abstract}")
    assert result.is_english is True
    assert result.detected_language == "ENGLISH"
    assert result.confidence > 0.5


def test_english_title_without_abstract_accepted() -> None:
    """2. English title alone is accepted without crashing."""
    title = "Deep Residual Learning for Image Recognition in Computer Vision"
    assert LanguageDetectionService.is_english(title, None) is True
    assert LanguageDetectionService.is_english(title, "") is True


def test_chinese_title_with_english_abstract_rejected() -> None:
    """Non-English title must be rejected even when abstract is English."""
    title = "矿山开采区水文地质综合勘查技术分析"
    abstract = (
        "Advanced Materials Science and Technology is a peer-reviewed open access "
        "journal published semi-annual online by Omniscient Pte. Ltd."
    )
    assert LanguageDetectionService.is_english(title, abstract) is False


def test_non_english_papers_rejected() -> None:
    """3. Non-English papers (German, French, Spanish, Russian) are rejected."""
    # German
    de_title = "Zur Elektrodynamik bewegter Körper"
    de_abstract = "Dass die Elektrodynamik Maxwells zu Asymmetrien führt, ist bekannt."
    assert LanguageDetectionService.is_english(de_title, de_abstract) is False

    # French
    fr_title = "Sur les groupes de Lie continus et finis"
    fr_abstract = "Une étude sur les théories modernes de la géométrie différentielle."
    assert LanguageDetectionService.is_english(fr_title, fr_abstract) is False

    # Spanish
    es_title = "Estudio sobre el impacto de la inteligencia artificial"
    es_abstract = "El desarrollo de redes neuronales profundas en la educación universitaria."
    assert LanguageDetectionService.is_english(es_title, es_abstract) is False


def test_empty_and_whitespace_text_rejected_safely() -> None:
    """4. Empty strings and whitespace are rejected safely without crashing."""
    assert LanguageDetectionService.is_english("", "") is False
    assert LanguageDetectionService.is_english("   ", "   ") is False
    assert LanguageDetectionService.is_english(None, None) is False

    res = LanguageDetectionService.detect_language("")
    assert res.is_english is False
    assert res.detected_language is None


def test_missing_abstract_does_not_crash() -> None:
    """5. None abstract with valid English title evaluates properly."""
    assert LanguageDetectionService.is_english("Generative Adversarial Networks", None) is True


def test_ambiguous_short_symbolic_text_handling() -> None:
    """6. Numbers / symbols alone do not falsely register as English."""
    assert LanguageDetectionService.is_english("12345 67890", "") is False


# ---------------------------------------------------------------------------
# 7, 10. Robustness and Exception Handling
# ---------------------------------------------------------------------------


def test_detector_exception_handled_gracefully() -> None:
    """7. Any internal detector exception returns is_english=False safely."""
    with patch.object(
        LanguageDetectionService,
        "_get_detector",
        side_effect=RuntimeError("Lingua crashed"),
    ):
        assert LanguageDetectionService.is_english("Some English Title") is False

        res = LanguageDetectionService.detect_language("Some English Title")
        assert res.is_english is False
        assert res.detected_language in ("UNKNOWN", "ERROR")


def test_is_english_skips_expensive_confidence_scoring() -> None:
    """Filter path must not compute per-paper confidence values."""
    mock_detector = MagicMock()
    mock_detector.detect_language_of.return_value = Language.ENGLISH

    with patch.object(LanguageDetectionService, "_get_detector", return_value=mock_detector):
        assert LanguageDetectionService.is_english("Attention Is All You Need", "An abstract.") is True

    assert mock_detector.detect_language_of.call_count == 2
    mock_detector.compute_language_confidence_values.assert_not_called()


def test_preload_builds_detector_once() -> None:
    LanguageDetectionService._detector = None
    LanguageDetectionService.preload()
    first = LanguageDetectionService._detector
    LanguageDetectionService.preload()
    assert LanguageDetectionService._detector is first




def test_detect_language_exception_in_inner_detector() -> None:
    """7b. Exception inside detector.detect_language_of returns ERROR result."""
    mock_detector = MagicMock()
    mock_detector.detect_language_of.side_effect = Exception("C-extension memory fault")

    with patch.object(LanguageDetectionService, "_get_detector", return_value=mock_detector):
        res = LanguageDetectionService.detect_language("Quantum Mechanics")
        assert res.is_english is False
        assert res.detected_language == "ERROR"


# ---------------------------------------------------------------------------
# 8-9. CandidateFilterService Integration Tests
# ---------------------------------------------------------------------------


def test_candidate_filter_service_filters_non_english_papers() -> None:
    """8, 9. CandidateFilterService filters out non-English papers and tracks count."""
    filter_svc = CandidateFilterService()

    candidates = [
        # 1. Valid English paper
        SpotlightCandidate(
            paper_id="en_paper_1",
            title="Reinforcement Learning for Autonomous Drone Navigation",
            abstract="We present an autonomous navigation framework using deep reinforcement learning.",
            url="https://example.com/en1",
            query='("drone")',
            spotlight_type=SpotlightType.influential_research,
            citation_count=50,
            year=2024,
        ),
        # 2. German paper (should be filtered by language)
        SpotlightCandidate(
            paper_id="de_paper_1",
            title="Entwicklung autonomer Systeme in der Luftfahrt",
            abstract="Eine umfassende Untersuchung über unbemannte Flugzeuge und maschinelles Lernen.",
            url="https://example.com/de1",
            query='("drone")',
            spotlight_type=SpotlightType.influential_research,
            citation_count=50,
            year=2024,
        ),
        # 3. Spanish paper (should be filtered by language)
        SpotlightCandidate(
            paper_id="es_paper_1",
            title="Diseño e implementación de redes neuronales convolucionales",
            abstract="Un estudio empírico del procesamiento digital de imágenes biomédicas.",
            url="https://example.com/es1",
            query='("drone")',
            spotlight_type=SpotlightType.influential_research,
            citation_count=50,
            year=2024,
        ),
        # 4. Another valid English paper
        SpotlightCandidate(
            paper_id="en_paper_2",
            title="Self-Supervised Learning for Graph Neural Networks",
            abstract="Graph representation learning has emerged as a powerful paradigm for relational data.",
            url="https://example.com/en2",
            query='("drone")',
            spotlight_type=SpotlightType.influential_research,
            citation_count=80,
            year=2025,
        ),
    ]

    result = filter_svc.filter_candidates(candidates)

    assert isinstance(result, CandidateFilterResult)
    assert result.original_count == 4
    assert result.language_filtered_count == 2
    assert result.final_count == 2
    remaining_ids = [c.paper_id for c in result.candidates]
    assert remaining_ids == ["en_paper_1", "en_paper_2"]


def test_language_filter_runs_before_duplicates_and_history() -> None:
    """8b. Non-English papers are counted in language_filtered_count before duplicate checking."""
    filter_svc = CandidateFilterService()

    candidates = [
        # Non-English paper appearing twice
        SpotlightCandidate(
            paper_id="de_dup_1",
            title="Fortschritte in der theoretischen Physik",
            abstract="Eine methodische Übersicht über Quantenfeldtheorie.",
            url="https://example.com/de_dup1",
            query='("physics")',
            spotlight_type=SpotlightType.latest_research,
            year=2026,
        ),
        SpotlightCandidate(
            paper_id="de_dup_1",
            title="Fortschritte in der theoretischen Physik",
            abstract="Eine methodische Übersicht über Quantenfeldtheorie.",
            url="https://example.com/de_dup1",
            query='("physics")',
            spotlight_type=SpotlightType.latest_research,
            year=2026,
        ),
    ]

    result = filter_svc.filter_candidates(candidates)
    assert result.original_count == 2
    assert result.language_filtered_count == 2
    assert result.duplicate_count == 0
    assert result.final_count == 0
