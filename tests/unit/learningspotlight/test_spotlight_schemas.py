from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from apps.learningspotlight.schemas import (
    LearningSpotlight,
    LearningSpotlightAuthor,
    LearningSpotlightEngagement,
    LearningSpotlightPaper,
    SpotlightDescription,
    extract_recommended_cycle_name,
    extract_recommended_papers,
    format_recommended_cycle_name,
    get_spotlight_description,
)
from common.enums import SpotlightFeedback, SpotlightType


def _valid_paper(**overrides) -> LearningSpotlightPaper:
    data = {
        "paper_id": "abc123",
        "title": "Example Paper",
        "authors": [LearningSpotlightAuthor(author_id="a1", name="Ada")],
        "abstract": "An abstract.",
        "venue": "NeurIPS",
        "year": 2026,
        "citation_count": 42,
        "url": "https://example.com/paper",
    }
    data.update(overrides)
    return LearningSpotlightPaper.model_validate(data)


def _valid_spotlight(**overrides) -> LearningSpotlight:
    data = {
        "version": 2,
        "cycle_day": 2,
        "spotlight_type": SpotlightType.country_perspective,
        "query": '("computer science"|"artificial intelligence")',
        "paper": _valid_paper(),
        "score": 91.5,
        "generated_at": datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone.utc),
        "engagement": LearningSpotlightEngagement(),
    }
    data.update(overrides)
    return LearningSpotlight.model_validate(data)


def test_valid_spotlight_with_all_fields() -> None:
    spotlight = _valid_spotlight()
    assert spotlight.version == 2
    assert spotlight.cycle_day == 2
    assert spotlight.spotlight_type is SpotlightType.country_perspective
    assert spotlight.score == 91.5
    assert spotlight.paper.paper_id == "abc123"
    assert spotlight.paper.citation_count == 42
    assert spotlight.paper.authors[0].name == "Ada"
    assert spotlight.engagement.is_read is False
    assert spotlight.engagement.is_saved is False
    assert spotlight.engagement.feedback is False



def test_valid_spotlight_feedback_null() -> None:
    spotlight = _valid_spotlight(
        engagement=LearningSpotlightEngagement(
            is_read=False, is_saved=False, feedback=False
        )
    )
    assert spotlight.engagement.feedback is False


def test_valid_spotlight_feedback_useful() -> None:
    spotlight = _valid_spotlight(
        engagement=LearningSpotlightEngagement(feedback=True)
    )
    assert spotlight.engagement.feedback is True


def test_valid_spotlight_feedback_not_useful() -> None:
    spotlight = _valid_spotlight(
        engagement=LearningSpotlightEngagement(feedback=True)
    )
    assert spotlight.engagement.feedback is True


def test_invalid_spotlight_type_rejected() -> None:
    with pytest.raises(ValidationError):
        _valid_spotlight(spotlight_type="not_a_real_type")


@pytest.mark.parametrize(
    ("spotlight_type", "expected_subtitle"),
    [
        (SpotlightType.leading_thinker, "Articles matched to your major, minor, and academic interests"),
        (SpotlightType.country_perspective, "Articles based on your location of study"),
        (SpotlightType.influential_research, "Articles trending in your field"),
        (SpotlightType.latest_research, "Articles based on the latest research in your field"),
        (SpotlightType.beyond_your_field, "Articles outside of your field to broaden your knowledge"),
    ],
)
def test_get_spotlight_description_maps_spotlight_type(
    spotlight_type: SpotlightType,
    expected_subtitle: str,
) -> None:
    description = get_spotlight_description(spotlight_type)
    assert description["title"] == "Learning Spotlight"
    assert description["subtitle"] == expected_subtitle
    assert set(description.keys()) == {"title", "subtitle"}


def test_get_spotlight_description_rejects_invalid_type() -> None:
    with pytest.raises(ValueError, match="Unknown spotlight type"):
        get_spotlight_description("not_a_real_type")


def test_format_recommended_cycle_name() -> None:
    assert format_recommended_cycle_name(SpotlightType.leading_thinker) == "Leading Thinker"
    assert format_recommended_cycle_name("country_perspective") == "Country Perspective"
    assert format_recommended_cycle_name(None) is None
    assert format_recommended_cycle_name("") is None
    assert extract_recommended_cycle_name({"spotlight_type": "latest_research"}) == "Latest Research"
    assert extract_recommended_cycle_name(None) is None
    assert extract_recommended_cycle_name({}) is None


def test_extract_recommended_papers_preserves_order() -> None:
    paper_ids, paper_titles = extract_recommended_papers(
        {
            "papers": [
                {
                    "paper_id": "003e3a6be8537162fd112b3a0a51a6063b640997",
                    "title": "Noether Symmetries and Covariant Conservation Laws",
                },
                {"paper_id": "paper-2", "title": "Paper Two Title"},
            ]
        }
    )
    assert paper_ids == [
        "003e3a6be8537162fd112b3a0a51a6063b640997",
        "paper-2",
    ]
    assert paper_titles == [
        "Noether Symmetries and Covariant Conservation Laws",
        "Paper Two Title",
    ]
    assert extract_recommended_papers(None) == ([], [])
    assert extract_recommended_papers({"paper": {"paper_id": "legacy", "title": "Legacy"}}) == (
        ["legacy"],
        ["Legacy"],
    )


@pytest.mark.parametrize(
    ("cycle_day", "spotlight_type", "expected_subtitle"),
    [
        (1, SpotlightType.leading_thinker, "Articles matched to your major, minor, and academic interests"),
        (2, SpotlightType.country_perspective, "Articles based on your location of study"),
        (3, SpotlightType.influential_research, "Articles trending in your field"),
        (4, SpotlightType.latest_research, "Articles based on the latest research in your field"),
        (5, SpotlightType.beyond_your_field, "Articles outside of your field to broaden your knowledge"),
    ],
)
def test_spotlight_description_follows_spotlight_type(
    cycle_day: int,
    spotlight_type: SpotlightType,
    expected_subtitle: str,
) -> None:
    spotlight = _valid_spotlight(cycle_day=cycle_day, spotlight_type=spotlight_type)
    assert isinstance(spotlight.description, SpotlightDescription)
    assert spotlight.description.title == "Learning Spotlight"
    dumped = spotlight.model_dump(mode="json")
    assert dumped["description"] == {
        "title": "Learning Spotlight",
        "subtitle": expected_subtitle,
    }
    assert dumped["cycle_day"] == cycle_day
    assert dumped["spotlight_type"] == spotlight_type.value
    assert dumped["query"] == spotlight.query
    assert "papers" in dumped
    assert "generated_at" in dumped
    assert "version" in dumped


def test_spotlight_description_follows_remapped_cycle_type() -> None:
    spotlight = _valid_spotlight(
        cycle_day=1,
        spotlight_type=SpotlightType.latest_research,
    )
    assert spotlight.spotlight_type is SpotlightType.latest_research
    assert spotlight.description.subtitle == (
        "Articles based on the latest research in your field"
    )


@pytest.mark.parametrize("cycle_day", [0, 6, -1, 99])
def test_cycle_day_outside_range_rejected(cycle_day: int) -> None:
    with pytest.raises(ValidationError):
        _valid_spotlight(cycle_day=cycle_day)


def test_is_read_and_is_saved_are_booleans() -> None:
    engagement = LearningSpotlightEngagement.model_validate(
        {"is_read": True, "is_saved": True, "feedback": None}
    )
    assert engagement.is_read is True
    assert engagement.is_saved is True
    with pytest.raises(ValidationError):
        LearningSpotlightEngagement.model_validate({"is_read": "yes"})


def test_query_preserved_exactly() -> None:
    query = '("computer science"|"artificial intelligence")'
    spotlight = _valid_spotlight(query=query)
    assert spotlight.query == query
    dumped = spotlight.model_dump()
    assert dumped["query"] == query


def test_paper_metadata_parsed_correctly() -> None:
    paper = LearningSpotlightPaper.model_validate(
        {
            "paper_id": "p99",
            "title": "Title",
            "authors": [{"author_id": "x", "name": "Grace"}],
            "abstract": "Abs",
            "venue": "ICML",
            "year": 2025,
            "citation_count": 7,
            "url": "https://example.com/p99",
        }
    )
    assert paper.paper_id == "p99"
    assert paper.venue == "ICML"
    assert paper.year == 2025
    assert paper.authors[0].author_id == "x"
    dumped = paper.model_dump()
    assert set(dumped.keys()) == {
        "paper_id",
        "title",
        "authors",
        "abstract",
        "venue",
        "year",
        "citation_count",
        "url",
        "score",
        "engagement",
    }


def test_spotlight_dump_is_archive_compatible_snapshot() -> None:
    """V2 dump includes all fields needed for historical log JSONB payloads."""
    spotlight = _valid_spotlight()
    snapshot = spotlight.model_dump(mode="json")
    assert snapshot["version"] == 2
    assert "cycle_day" in snapshot
    assert "spotlight_type" in snapshot
    assert "query" in snapshot
    assert "papers" in snapshot
    assert "generated_at" in snapshot
    # Round-trip through the same schema (as a log archive would later reload).
    restored = LearningSpotlight.model_validate(snapshot)
    assert restored.paper.paper_id == spotlight.paper.paper_id
    assert restored.description.title == "Learning Spotlight"
    assert restored.description.subtitle == "Articles based on your location of study"

