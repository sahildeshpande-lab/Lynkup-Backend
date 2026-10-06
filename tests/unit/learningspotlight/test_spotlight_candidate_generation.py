"""Tests for Step 6 — Learning Spotlight V2 candidate generation strategies.

All Semantic Scholar calls are mocked.  These tests verify:
* Query construction per strategy
* Candidate model structure
* Strategy-specific filtering (citations, year, field exclusion)
* Query preservation on candidates
* Graceful degradation on SS failures
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.learningspotlight.config import settings as spotlight_settings
from apps.learningspotlight.schemas import (
    SpotlightCandidate,
    SpotlightUserContext,
)
from apps.learningspotlight.services.beyond_your_field_strategy import (
    BeyondYourFieldStrategy,
)
from apps.learningspotlight.services.country_perspective_strategy import (
    CountryPerspectiveStrategy,
)
from apps.learningspotlight.services.influential_research_strategy import (
    InfluentialResearchStrategy,
    MIN_INFLUENTIAL_CITATIONS,
)
from apps.learningspotlight.services.latest_research_strategy import (
    LATEST_RESEARCH_MAX_AGE_YEARS,
    LatestResearchStrategy,
    _compute_year_filter,
)
from apps.learningspotlight.services.query_helpers import (
    build_country_perspective_query,
    build_spotlight_query,
    extract_country_perspective_concept,
    fields_of_study_from_user_interests,
    fields_of_study_from_user_profile,
    raw_papers_to_candidates,
    select_country_perspective_concepts,
)
from common.enums import SpotlightType

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_SS_MOCK_PATH = (
    "apps.learningspotlight.services.semantic_scholar_adapter.search_papers_v2"
)


def _make_context(**overrides: Any) -> SpotlightUserContext:
    defaults: dict[str, Any] = {
        "user_id": uuid4(),
        "country": "India",
        "major": "Computer Science",
        "minor": "Mathematics",
        "interests": ["NLP", "Robotics"],
        "extracted_keywords": {
            "major": ["Computer Science"],
            "minor": ["Mathematics"],
            "interests": ["NLP", "Robotics"],
            "engagement_keywords": {"transformers": 5, "deep learning": 3},
        },
    }
    defaults.update(overrides)
    return SpotlightUserContext(**defaults)


def _make_paper(
    paper_id: str = "p1",
    title: str = "Test Paper",
    citation_count: int = 50,
    year: int = 2026,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "paperId": paper_id,
        "title": title,
        "authors": [
            {"authorId": "a1", "name": "Alice", "affiliations": ["MIT"]},
            {"authorId": "a2", "name": "Bob"},
        ],
        "abstract": "An abstract about testing.",
        "venue": "NeurIPS",
        "citationCount": citation_count,
        "url": f"https://example.com/{paper_id}",
        "publicationDate": f"{year}-06-15",
        "year": year,
        "fieldsOfStudy": ["Computer Science"],
        "s2FieldsOfStudy": [
            {"category": "Computer Science", "source": "s2-fos-model"},
        ],
        "openAccessPdf": {
            "url": f"https://example.com/{paper_id}.pdf",
            "status": "GOLD",
            "license": "CC-BY",
        },
        **extra,
    }


def _make_ss_response(*papers: dict[str, Any]) -> tuple[dict[str, Any], int]:
    return {"data": list(papers), "total": len(papers)}, 200


def _empty_ss_response() -> tuple[dict[str, Any], int | None]:
    return {"data": [], "total": 0}, None


# ===================================================================
# QUERY HELPERS
# ===================================================================


class TestBuildSpotlightQuery:
    def test_builds_query_from_keywords(self) -> None:
        kw = {
            "major": ["Computer Science"],
            "interests": ["NLP"],
        }
        query = build_spotlight_query(kw)
        assert query  # non-empty
        assert "Computer Science" in query or "computer science" in query.lower()

    def test_builds_query_without_minor(self) -> None:
        """Minor is optional — major alone still produces a searchable query."""
        kw = {
            "major": ["Computer Science"],
            "minor": [],
            "interests": [],
        }
        query = build_spotlight_query(kw)
        assert query
        assert "computer science" in query.lower()

    def test_exclude_fields_removes_major_minor(self) -> None:
        kw = {
            "major": ["Computer Science"],
            "minor": ["Mathematics"],
            "interests": ["NLP"],
        }
        query = build_spotlight_query(kw, exclude_fields=["major", "minor"])
        assert query
        # major/minor should be excluded
        lower = query.lower()
        assert "computer science" not in lower
        assert "mathematics" not in lower
        assert "nlp" in lower

    def test_extra_terms_appended(self) -> None:
        kw = {"major": ["Physics"]}
        query = build_spotlight_query(kw, extra_terms=["India"])
        assert "India" in query

    def test_empty_keywords_returns_empty(self) -> None:
        assert build_spotlight_query({}) == ""
        assert build_spotlight_query(None) == ""  # type: ignore[arg-type]

    def test_extra_terms_only_when_no_keywords(self) -> None:
        query = build_spotlight_query({}, extra_terms=["sustainability"])
        assert query == "sustainability"

    def test_pair_extra_terms_ands_each_profile_signal_with_country(self) -> None:
        query = build_spotlight_query(
            {
                "major": ["History"],
                "minor": ["Literature"],
                "interests": ["Pragmatics", "Semantics"],
            },
            extra_terms=["United States"],
            pair_extra_terms=True,
        )
        lower = query.lower()
        assert '(history+"united states")' in lower
        assert '(literature+"united states")' in lower
        assert '(pragmatics+"united states")' in lower
        assert '(semantics+"united states")' in lower
        assert "united states" in lower
        assert query.count("|") >= 3
        assert '("history"' not in lower

    def test_verbose_interests_become_concept_groups(self) -> None:
        from urllib.parse import quote_plus

        from apps.learningspotlight.services.query_helpers import (
            build_semantic_scholar_query,
            encode_spotlight_query,
        )

        topics = [
            "Applied Critical Thinking In Acting",
            "Drama/Theatre Arts and Stagecraft",
            "advanced visual composition",
        ]
        query = build_semantic_scholar_query(topics)
        lower = query.lower()
        assert "applied critical thinking in acting" not in lower
        assert "drama/theatre arts and stagecraft" not in lower
        assert "advanced visual composition" not in lower
        assert "applied|critical|thinking|acting" not in lower
        assert '"critical thinking"+acting' in lower
        assert "drama+theatre+stagecraft" in lower
        assert '"visual composition"' in lower
        assert query.count("|") == 2
        encoded = encode_spotlight_query(query)
        assert encoded == quote_plus(query)
        assert "%28" in encoded
        assert "%7C" in encoded

    def test_country_is_anded_inside_each_concept_group(self) -> None:
        from apps.learningspotlight.services.query_helpers import (
            build_semantic_scholar_query,
        )

        topics = [
            "Applied Critical Thinking In Acting",
            "Drama/Theatre Arts and Stagecraft",
            "advanced visual composition",
        ]
        query = build_semantic_scholar_query(topics, country="United States")
        lower = query.lower()
        assert '"critical thinking"+acting+"united states"' in lower
        assert "drama+theatre+stagecraft+" in lower
        assert '"united states"' in lower
        assert query.count("|") == 2


class TestFieldsOfStudyFromProfile:
    def test_major_minor_come_from_profile_labels(self) -> None:
        context = _make_context(
            major="History",
            minor="Literature",
            interests=["Pragmatics", "Semantics"],
            country="United States",
            extracted_keywords={
                "major": ["History"],
                "minor": ["Literature"],
                "interests": ["Pragmatics", "Semantics"],
            },
        )
        assert fields_of_study_from_user_profile(context) == ["History", "Literature"]
        assert fields_of_study_from_user_interests(context) == [
            "Pragmatics",
            "Semantics",
        ]

    def test_country_and_interests_are_not_major_minor_fields_of_study(self) -> None:
        context = _make_context(
            major="Computer Science",
            minor="Mathematics",
            interests=["NLP"],
            country="United States",
            extracted_keywords={
                "major": ["Computer Science"],
                "minor": ["Mathematics"],
                "interests": ["NLP"],
            },
        )
        assert fields_of_study_from_user_profile(context) == [
            "Computer Science",
            "Mathematics",
        ]
        assert "United States" not in fields_of_study_from_user_profile(context)
        assert fields_of_study_from_user_interests(context) == ["NLP"]

    def test_placeholder_major_minor_are_dropped(self) -> None:
        context = _make_context(
            major="N/A",
            minor="None",
            extracted_keywords={
                "major": ["Na", "", "None"],
                "minor": ["N/A", "null"],
            },
        )
        assert fields_of_study_from_user_profile(context) == []

    def test_valid_major_kept_when_minor_is_placeholder(self) -> None:
        context = _make_context(
            major="History",
            minor="N/A",
            extracted_keywords={
                "major": ["History"],
                "minor": ["None"],
            },
        )
        assert fields_of_study_from_user_profile(context) == ["History"]


class TestRawPapersToCandidates:
    def test_converts_papers_to_candidates(self) -> None:
        papers = [_make_paper("p1"), _make_paper("p2")]
        candidates = raw_papers_to_candidates(
            papers,
            query="test query",
            spotlight_type=SpotlightType.influential_research,
        )
        assert len(candidates) == 2
        assert all(isinstance(c, SpotlightCandidate) for c in candidates)

    def test_query_preserved_on_every_candidate(self) -> None:
        papers = [_make_paper()]
        candidates = raw_papers_to_candidates(
            papers,
            query='("AI"|"ML")',
            spotlight_type=SpotlightType.latest_research,
        )
        assert candidates[0].query == '("AI"|"ML")'

    def test_spotlight_type_set_on_candidate(self) -> None:
        papers = [_make_paper()]
        candidates = raw_papers_to_candidates(
            papers,
            query="q",
            spotlight_type=SpotlightType.country_perspective,
        )
        assert candidates[0].spotlight_type == SpotlightType.country_perspective

    def test_skips_papers_without_paper_id(self) -> None:
        papers = [{"paperId": None, "title": "Bad"}, _make_paper("good")]
        candidates = raw_papers_to_candidates(
            papers,
            query="q",
            spotlight_type=SpotlightType.latest_research,
        )
        assert len(candidates) == 1
        assert candidates[0].paper_id == "good"

    def test_skips_papers_without_abstract(self) -> None:
        no_abstract = _make_paper("missing_abstract")
        no_abstract.pop("abstract")
        empty_abstract = _make_paper("empty_abstract")
        empty_abstract["abstract"] = "   "
        papers = [no_abstract, empty_abstract, _make_paper("valid")]
        candidates = raw_papers_to_candidates(
            papers,
            query="q",
            spotlight_type=SpotlightType.latest_research,
        )
        assert len(candidates) == 1
        assert candidates[0].paper_id == "valid"

    def test_fields_of_study_in_metadata(self) -> None:
        papers = [_make_paper(fieldsOfStudy=["Biology", "Chemistry"])]
        candidates = raw_papers_to_candidates(
            papers,
            query="q",
            spotlight_type=SpotlightType.beyond_your_field,
        )
        assert candidates[0].metadata["fields_of_study"] == ["Biology", "Chemistry"]

    def test_citation_count_preserved(self) -> None:
        papers = [_make_paper(citation_count=42)]
        candidates = raw_papers_to_candidates(
            papers,
            query="q",
            spotlight_type=SpotlightType.influential_research,
        )
        assert candidates[0].citation_count == 42

    def test_extra_metadata_merged(self) -> None:
        papers = [_make_paper()]
        candidates = raw_papers_to_candidates(
            papers,
            query="q",
            spotlight_type=SpotlightType.country_perspective,
            extra_metadata={"country": "India"},
        )
        assert candidates[0].metadata["country"] == "India"

    def test_year_from_publication_date_fallback(self) -> None:
        paper = _make_paper()
        paper.pop("year")  # remove explicit year field
        paper["publicationDate"] = "2025-03-10"
        candidates = raw_papers_to_candidates(
            [paper],
            query="q",
            spotlight_type=SpotlightType.latest_research,
        )
        assert candidates[0].year == 2025

    def test_skips_papers_without_usable_open_access_pdf(self) -> None:
        missing = _make_paper("missing")
        missing.pop("openAccessPdf")
        null_pdf = _make_paper("null_pdf", openAccessPdf=None)
        empty_url = _make_paper(
            "empty_url",
            openAccessPdf={"url": "", "status": "GOLD", "license": "CC-BY"},
        )
        null_status = _make_paper(
            "null_status",
            openAccessPdf={
                "url": "https://example.com/null_status.pdf",
                "status": None,
                "license": "CC-BY",
            },
        )
        null_license = _make_paper(
            "null_license",
            openAccessPdf={
                "url": "https://example.com/null_license.pdf",
                "status": "GOLD",
                "license": None,
            },
        )
        papers = [
            missing,
            null_pdf,
            empty_url,
            null_status,
            null_license,
            _make_paper("valid"),
        ]
        candidates = raw_papers_to_candidates(
            papers,
            query="q",
            spotlight_type=SpotlightType.latest_research,
        )
        assert len(candidates) == 1
        assert candidates[0].paper_id == "valid"


# ===================================================================
# COUNTRY PERSPECTIVE
# ===================================================================


class TestSelectCountryPerspectiveConcepts:
    def test_major_only(self) -> None:
        concepts = select_country_perspective_concepts(
            {"major": ["Information Studies"]},
        )
        assert concepts == ["Information Studies"]

    def test_major_and_minor(self) -> None:
        concepts = select_country_perspective_concepts(
            {
                "major": ["Political Science and Government"],
                "minor": ["Brand Designer"],
            },
        )
        assert concepts == [
            "Political Science and Government",
            "Brand Designer",
        ]

    def test_major_minor_and_three_interests(self) -> None:
        concepts = select_country_perspective_concepts(
            {
                "major": ["Law"],
                "minor": ["Economics"],
                "interests": ["Constitutional Law", "Criminal Law", "Civil Law"],
            },
        )
        assert concepts == [
            "Law",
            "Economics",
            "Constitutional Law",
            "Criminal Law",
            "Civil Law",
        ]

    def test_max_concepts_enforced_with_many_interests(self) -> None:
        interests = [f"Interest Topic {i}" for i in range(12)]
        concepts = select_country_perspective_concepts(
            {
                "major": ["Banking, Corporate, Finance, and Securities Law"],
                "minor": ["American/US Law/Legal Studies/Jurisprudence"],
                "interests": interests,
            },
            max_concepts=5,
        )
        assert len(concepts) == 5
        assert concepts[0] == "Banking, Corporate, Finance, and Securities Law"
        assert concepts[1] == "American/US Law/Legal Studies/Jurisprudence"
        assert concepts[2] == "Interest Topic 0"
        assert concepts[3] == "Interest Topic 1"
        assert concepts[4] == "Interest Topic 2"

    def test_preserves_information_studies(self) -> None:
        assert extract_country_perspective_concept("Information Studies") == (
            "Information Studies"
        )

    def test_preserves_political_science_and_government(self) -> None:
        assert extract_country_perspective_concept(
            "Political Science and Government"
        ) == "Political Science and Government"

    def test_hierarchical_interest_extraction(self) -> None:
        cases = [
            (
                "Applied Constitutional Law In American/US Law/Legal Studies/Jurisprudence",
                "Constitutional Law",
            ),
            (
                "Advanced Criminal Law In American/US Law/Legal Studies/Jurisprudence",
                "Criminal Law",
            ),
            (
                "Applied Contract Law In American/US Law/Legal Studies/Jurisprudence",
                "Contract Law",
            ),
            (
                "Advanced Investment Analysis In Banking, Corporate, Finance, Securities Law",
                "Investment Analysis",
            ),
            (
                "Advanced Portfolio Management In Banking, Corporate, Finance, Securities Law",
                "Portfolio Management",
            ),
        ]
        for raw, expected in cases:
            assert extract_country_perspective_concept(raw) == expected

    def test_deduplicates_concepts(self) -> None:
        concepts = select_country_perspective_concepts(
            {
                "major": ["Constitutional Law"],
                "interests": [
                    "Applied Constitutional Law In American/US Law/Legal Studies/Jurisprudence",
                    "Constitutional Law",
                ],
            },
        )
        assert concepts == ["Constitutional Law"]

    def test_major_preserved_full_even_with_hierarchy_markers(self) -> None:
        concepts = select_country_perspective_concepts(
            {
                "major": [
                    "Applied Constitutional Law In American/US Law/Legal Studies/Jurisprudence"
                ],
            },
        )
        assert concepts == [
            "Applied Constitutional Law In American/US Law/Legal Studies/Jurisprudence"
        ]


class TestBuildCountryPerspectiveQuery:
    """Country Perspective-only query builder: preserve phrases, AND country, OR clauses."""

    def test_preserves_information_studies_with_ghana(self) -> None:
        query = build_country_perspective_query(
            ["Information Studies"],
            country="Ghana",
        )
        assert query == '("Information Studies"+Ghana)'
        assert "information" not in query.replace("Information Studies", "")

    def test_preserves_political_science_and_government_with_ghana(self) -> None:
        query = build_country_perspective_query(
            ["Political Science and Government"],
            country="Ghana",
        )
        assert query == '("Political Science and Government"+Ghana)'
        assert "+government" not in query.lower()
        assert '|"' not in query  # single clause — no and-split into OR parts

    def test_preserves_brand_designer_with_ghana(self) -> None:
        query = build_country_perspective_query(
            ["Brand Designer"],
            country="Ghana",
        )
        assert query == '("Brand Designer"+Ghana)'

    def test_combined_or_query_for_ghana_profile(self) -> None:
        query = build_country_perspective_query(
            [
                "Information Studies",
                "Political Science and Government",
                "Brand Designer",
            ],
            country="Ghana",
        )
        assert query == (
            '("Information Studies"+Ghana)|'
            '("Political Science and Government"+Ghana)|'
            '("Brand Designer"+Ghana)'
        )
        assert query.count("|") == 2
        assert "(information+" not in query.lower()
        assert "information+ghana" not in query.lower()
        assert '"Political Science"+government' not in query
        # Must not and-split into separate government token AND'd with Ghana alone
        assert "(government+Ghana)" not in query
        assert "(Government+Ghana)" not in query

    def test_does_not_strip_studies_or_split_on_and(self) -> None:
        query = build_country_perspective_query(
            ["Information Studies", "Political Science and Government"],
            country="Ghana",
        )
        assert "Information Studies" in query
        assert "Political Science and Government" in query
        assert query.count("(") == 2

    def test_country_only_when_no_topics(self) -> None:
        assert build_country_perspective_query([], country="Ghana") == "(Ghana)"
        assert build_country_perspective_query(None, country=None) == ""

    def test_no_country_still_preserves_phrases(self) -> None:
        query = build_country_perspective_query(
            ["Information Studies", "Brand Designer"],
            country=None,
        )
        assert query == '("Information Studies")|("Brand Designer")'

    def test_hierarchical_concepts_combined_with_country(self) -> None:
        concepts = select_country_perspective_concepts(
            {
                "major": ["Banking, Corporate, Finance, and Securities Law"],
                "minor": ["American/US Law/Legal Studies/Jurisprudence"],
                "interests": [
                    "Applied Constitutional Law In American/US Law/Legal Studies/Jurisprudence",
                    "Advanced Criminal Law In American/US Law/Legal Studies/Jurisprudence",
                    "Applied Contract Law In American/US Law/Legal Studies/Jurisprudence",
                ],
            },
        )
        query = build_country_perspective_query(concepts, country="United States")
        assert query == (
            '("Banking, Corporate, Finance, and Securities Law"+"United States")|'
            '("American/US Law/Legal Studies/Jurisprudence"+"United States")|'
            '("Constitutional Law"+"United States")|'
            '("Criminal Law"+"United States")|'
            '("Contract Law"+"United States")'
        )


class TestCountryPerspectiveStrategy:
    @pytest.mark.asyncio
    async def test_query_contains_country_and_field_signals(self) -> None:
        context = _make_context()
        captured_query = None

        async def mock_search(query, **kwargs):
            nonlocal captured_query
            captured_query = query
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            candidates = await CountryPerspectiveStrategy().get_candidates(context)

        assert captured_query is not None
        # Phrases preserved; country AND-paired from context.country
        assert '("Computer Science"+India)' in captured_query
        assert "(NLP+India)" in captured_query or '("NLP"+India)' in captured_query
        assert "India" in captured_query

    @pytest.mark.asyncio
    async def test_ghana_profile_preserves_meaningful_phrases(self) -> None:
        context = _make_context(
            country="Ghana",
            major=None,
            minor=None,
            interests=[
                "Information Studies",
                "Political Science and Government",
                "Brand Designer",
            ],
            extracted_keywords={
                "interests": [
                    "Information Studies",
                    "Political Science and Government",
                    "Brand Designer",
                ],
            },
        )
        captured_query = None
        captured_kwargs: dict[str, Any] = {}

        async def mock_search(query, **kwargs):
            nonlocal captured_query
            captured_query = query
            captured_kwargs.update(kwargs)
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            candidates = await CountryPerspectiveStrategy().get_candidates(context)

        assert captured_query == (
            '("Information Studies"+Ghana)|'
            '("Political Science and Government"+Ghana)|'
            '("Brand Designer"+Ghana)'
        )
        assert captured_kwargs.get("fields_of_study") is None
        assert (
            captured_kwargs.get("limit")
            == spotlight_settings.learning_spotlight_candidate_limit
        )
        assert candidates
        assert candidates[0].metadata.get("country") == "Ghana"

    @pytest.mark.asyncio
    async def test_fields_of_study_is_always_none(self) -> None:
        context = _make_context(
            country="United States",
            major="History",
            minor="Literature",
            interests=["Pragmatics", "Semantics"],
            extracted_keywords={
                "major": ["History"],
                "minor": ["Literature"],
                "interests": ["Pragmatics", "Semantics"],
            },
        )
        captured_query = None
        captured_kwargs: dict[str, Any] = {}

        async def mock_search(query, **kwargs):
            nonlocal captured_query
            captured_query = query
            captured_kwargs.update(kwargs)
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            candidates = await CountryPerspectiveStrategy().get_candidates(context)

        assert captured_query is not None
        assert '(History+"United States")' in captured_query
        assert '(Literature+"United States")' in captured_query
        assert '(Pragmatics+"United States")' in captured_query
        assert '(Semantics+"United States")' in captured_query
        assert captured_kwargs.get("fields_of_study") is None
        assert candidates
        assert candidates[0].metadata.get("country") == "United States"

    @pytest.mark.asyncio
    async def test_country_comes_from_context_not_extracted_keywords(self) -> None:
        context = _make_context(
            country="Ghana",
            extracted_keywords={
                "major": ["Information Studies"],
                "interests": ["Nigeria"],  # must not be treated as country
            },
        )
        captured_query = None

        async def mock_search(query, **kwargs):
            nonlocal captured_query
            captured_query = query
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            await CountryPerspectiveStrategy().get_candidates(context)

        assert captured_query is not None
        assert "+Ghana)" in captured_query
        # "Nigeria" may appear as an interest clause, but country pairing uses Ghana
        assert captured_query.count("+Ghana)") >= 1

    @pytest.mark.asyncio
    async def test_or_query_succeeds_even_if_one_concept_alone_would_return_zero(
        self,
    ) -> None:
        """Combined OR search returns papers; a zero-hit clause must not fail the search."""
        context = _make_context(
            country="Ghana",
            major=None,
            minor=None,
            interests=[
                "Information Studies",
                "Political Science and Government",
                "Brand Designer",
            ],
            extracted_keywords={
                "interests": [
                    "Information Studies",
                    "Political Science and Government",
                    "Brand Designer",
                ],
            },
        )

        async def mock_search(query, **kwargs):
            # Semantic Scholar returns the union for OR queries — one clause
            # matching zero papers does not empty the whole response.
            assert "|" in query
            assert "Political Science and Government" in query
            return _make_ss_response(
                _make_paper("from_info_studies"),
                _make_paper("from_brand_designer"),
            )

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            candidates = await CountryPerspectiveStrategy().get_candidates(context)

        assert len(candidates) == 2
        assert {c.paper_id for c in candidates} == {
            "from_info_studies",
            "from_brand_designer",
        }

    @pytest.mark.asyncio
    async def test_candidate_metadata_contains_country(self) -> None:
        context = _make_context(country="Germany")
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await CountryPerspectiveStrategy().get_candidates(context)

        assert len(candidates) >= 1
        assert candidates[0].metadata["country"] == "Germany"

    @pytest.mark.asyncio
    async def test_candidates_returned_with_correct_model(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await CountryPerspectiveStrategy().get_candidates(context)

        assert all(isinstance(c, SpotlightCandidate) for c in candidates)
        assert all(
            c.spotlight_type == SpotlightType.country_perspective for c in candidates
        )

    @pytest.mark.asyncio
    async def test_no_country_still_returns_candidates(self) -> None:
        """If user has no country, query should still work (no country bias)."""
        context = _make_context(country=None)
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await CountryPerspectiveStrategy().get_candidates(context)

        assert len(candidates) >= 1

    @pytest.mark.asyncio
    async def test_ss_failure_returns_empty_list(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_empty_ss_response()):
            candidates = await CountryPerspectiveStrategy().get_candidates(context)

        assert candidates == []

    @pytest.mark.asyncio
    async def test_no_keywords_returns_empty(self) -> None:
        context = _make_context(extracted_keywords={})
        candidates = await CountryPerspectiveStrategy().get_candidates(context)
        assert candidates == []

    @pytest.mark.asyncio
    async def test_author_affiliations_in_metadata(self) -> None:
        """Author affiliations should be captured when present."""
        context = _make_context()
        paper = _make_paper()
        paper["authors"] = [
            {"authorId": "a1", "name": "Alice", "affiliations": ["IIT Delhi"]},
        ]
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(paper)):
            candidates = await CountryPerspectiveStrategy().get_candidates(context)

        assert len(candidates) == 1
        affs = candidates[0].metadata.get("author_affiliations", [])
        assert len(affs) >= 1
        assert "IIT Delhi" in affs[0]["affiliations"]

    @pytest.mark.asyncio
    async def test_semantic_scholar_external_error_propagates(self) -> None:
        from apps.learningspotlight.services.semantic_scholar_adapter import (
            SemanticScholarExternalError,
        )

        context = _make_context(country="Ghana")

        async def mock_search(query, **kwargs):
            raise SemanticScholarExternalError(
                "rate limited",
                status_code=429,
                retryable=True,
            )

        with (
            patch(_SS_MOCK_PATH, side_effect=mock_search),
            pytest.raises(SemanticScholarExternalError) as exc_info,
        ):
            await CountryPerspectiveStrategy().get_candidates(context)

        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_other_strategies_still_use_shared_query_builder(self) -> None:
        """Leading Thinker / Influential / Latest / Beyond must not use CP builder."""
        from pathlib import Path

        shared_strategies = [
            "leading_thinker_strategy.py",
            "influential_research_strategy.py",
            "latest_research_strategy.py",
            "beyond_your_field_strategy.py",
        ]
        for filename in shared_strategies:
            source = Path("apps/learningspotlight/services", filename).read_text(
                encoding="utf-8"
            )
            assert "build_country_perspective_query" not in source
            assert "search_spotlight_candidates" in source

        cp_source = Path(
            "apps/learningspotlight/services/country_perspective_strategy.py"
        ).read_text(encoding="utf-8")
        assert "build_country_perspective_query" in cp_source
        assert "fields_of_study=None" in cp_source
        assert "override_queries" in cp_source


class TestInfluentialResearchStrategy:
    @pytest.mark.asyncio
    async def test_papers_above_threshold_are_included(self) -> None:
        context = _make_context()
        papers = [
            _make_paper("high", citation_count=100),
            _make_paper("exact", citation_count=20),
        ]
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(*papers)):
            candidates = await InfluentialResearchStrategy().get_candidates(context)

        ids = {c.paper_id for c in candidates}
        assert "high" in ids
        assert "exact" in ids

    @pytest.mark.asyncio
    async def test_papers_below_threshold_are_excluded(self) -> None:
        context = _make_context()
        papers = [
            _make_paper("high", citation_count=100),
            _make_paper("low", citation_count=5),
            _make_paper("zero", citation_count=0),
        ]
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(*papers)):
            candidates = await InfluentialResearchStrategy().get_candidates(context)

        ids = {c.paper_id for c in candidates}
        assert "high" in ids
        assert "low" not in ids
        assert "zero" not in ids

    @pytest.mark.asyncio
    async def test_citation_count_preserved_on_candidates(self) -> None:
        context = _make_context()
        papers = [_make_paper("p1", citation_count=42)]
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(*papers)):
            candidates = await InfluentialResearchStrategy().get_candidates(context)

        assert candidates[0].citation_count == 42

    @pytest.mark.asyncio
    async def test_papers_with_null_citation_excluded(self) -> None:
        context = _make_context()
        paper = _make_paper("null_cite")
        paper["citationCount"] = None
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(paper)):
            candidates = await InfluentialResearchStrategy().get_candidates(context)

        assert len(candidates) == 0  # None treated as 0, below threshold

    @pytest.mark.asyncio
    async def test_query_preserved(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await InfluentialResearchStrategy().get_candidates(context)

        assert all(c.query for c in candidates)

    @pytest.mark.asyncio
    async def test_spotlight_type_set(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await InfluentialResearchStrategy().get_candidates(context)

        assert all(
            c.spotlight_type == SpotlightType.influential_research for c in candidates
        )

    @pytest.mark.asyncio
    async def test_ss_failure_returns_empty(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_empty_ss_response()):
            candidates = await InfluentialResearchStrategy().get_candidates(context)

        assert candidates == []

    @pytest.mark.asyncio
    async def test_threshold_constant_is_10(self) -> None:
        assert MIN_INFLUENTIAL_CITATIONS == 10

    @pytest.mark.asyncio
    async def test_does_not_send_fields_of_study(self) -> None:
        context = _make_context()
        captured_kwargs: dict[str, Any] = {}

        async def mock_search(query, **kwargs):
            captured_kwargs.update(kwargs)
            return _make_ss_response(_make_paper("high", citation_count=100))

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            await InfluentialResearchStrategy().get_candidates(context)

        assert captured_kwargs.get("fields_of_study") is None


# ===================================================================
# LATEST RESEARCH
# ===================================================================


class TestLatestResearchStrategy:
    def test_year_filter_is_dynamic(self) -> None:
        """Year filter should be based on current date, not hardcoded."""
        today = date(2026, 8, 23)
        assert _compute_year_filter(today) == "2025-"

    def test_year_filter_different_years(self) -> None:
        assert _compute_year_filter(date(2027, 1, 1)) == "2026-"
        assert _compute_year_filter(date(2025, 12, 31)) == "2024-"

    @pytest.mark.asyncio
    async def test_year_filter_passed_to_ss(self) -> None:
        context = _make_context()
        captured_kwargs: dict[str, Any] = {}

        async def mock_search(query, **kwargs):
            captured_kwargs.update(kwargs)
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            await LatestResearchStrategy().get_candidates(context)

        assert "year" in captured_kwargs
        assert captured_kwargs.get("fields_of_study") is None
        year_filter = captured_kwargs["year"]
        # Should be in format "YYYY-"
        assert year_filter.endswith("-")
        year_part = int(year_filter.rstrip("-"))
        # The start year should be within reasonable range
        from datetime import datetime, timezone

        current_year = datetime.now(timezone.utc).year
        expected_start = current_year - LATEST_RESEARCH_MAX_AGE_YEARS
        assert year_part == expected_start

    @pytest.mark.asyncio
    async def test_candidates_have_year(self) -> None:
        context = _make_context()
        papers = [_make_paper(year=2026)]
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(*papers)):
            candidates = await LatestResearchStrategy().get_candidates(context)

        assert candidates[0].year == 2026

    @pytest.mark.asyncio
    async def test_year_filter_in_metadata(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await LatestResearchStrategy().get_candidates(context)

        assert "year_filter" in candidates[0].metadata

    @pytest.mark.asyncio
    async def test_spotlight_type_set(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await LatestResearchStrategy().get_candidates(context)

        assert all(
            c.spotlight_type == SpotlightType.latest_research for c in candidates
        )

    @pytest.mark.asyncio
    async def test_query_preserved(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await LatestResearchStrategy().get_candidates(context)

        assert all(c.query for c in candidates)

    @pytest.mark.asyncio
    async def test_ss_failure_returns_empty(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_empty_ss_response()):
            candidates = await LatestResearchStrategy().get_candidates(context)

        assert candidates == []


# ===================================================================
# BEYOND YOUR FIELD
# ===================================================================


class TestBeyondYourFieldStrategy:
    @pytest.mark.asyncio
    async def test_major_minor_excluded_from_query(self) -> None:
        context = _make_context()
        captured_query = None

        async def mock_search(query, **kwargs):
            nonlocal captured_query
            captured_query = query
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            await BeyondYourFieldStrategy().get_candidates(context)

        assert captured_query is not None
        lower = captured_query.lower()
        # Major/minor should NOT be in the query
        assert "computer science" not in lower
        assert "mathematics" not in lower

    @pytest.mark.asyncio
    async def test_interests_keywords_drive_query(self) -> None:
        context = _make_context()
        captured_query = None

        async def mock_search(query, **kwargs):
            nonlocal captured_query
            captured_query = query
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            await BeyondYourFieldStrategy().get_candidates(context)

        assert captured_query is not None
        lower = captured_query.lower()
        # Should contain at least some interest/keyword signals
        has_signal = any(
            term in lower
            for term in ["nlp", "robotics", "transformers", "deep learning"]
        )
        assert has_signal

    @pytest.mark.asyncio
    async def test_uses_interest_query_without_major_minor_fields_of_study(
        self,
    ) -> None:
        context = _make_context(
            major="History",
            minor="Literature",
            interests=["Pragmatics", "Semantics"],
            extracted_keywords={
                "major": ["History"],
                "minor": ["Literature"],
                "interests": ["Pragmatics", "Semantics"],
            },
        )
        captured_query = None
        captured_kwargs: dict[str, Any] = {}

        async def mock_search(query, **kwargs):
            nonlocal captured_query
            captured_query = query
            captured_kwargs.update(kwargs)
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            await BeyondYourFieldStrategy().get_candidates(context)

        assert captured_query is not None
        lower = captured_query.lower()
        assert "pragmatics" in lower or "semantics" in lower
        assert "history" not in lower
        assert "literature" not in lower
        assert captured_kwargs.get("fields_of_study") is None

    @pytest.mark.asyncio
    async def test_does_not_retrieve_with_major_minor_fields_of_study(
        self,
    ) -> None:
        """Environmental Science papers must not be blocked by History,Literature FoS."""
        context = _make_context(
            major="History",
            minor="Literature",
            interests=["climate policy"],
            extracted_keywords={
                "major": ["History"],
                "minor": ["Literature"],
                "interests": ["climate policy"],
            },
        )
        captured_kwargs: dict[str, Any] = {}

        async def mock_search(query, **kwargs):
            captured_kwargs.update(kwargs)
            paper = _make_paper("env", fieldsOfStudy=["Environmental Science"])
            return _make_ss_response(paper)

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            candidates = await BeyondYourFieldStrategy().get_candidates(context)

        assert captured_kwargs.get("fields_of_study") not in (
            ["History", "Literature"],
            ["History"],
            ["Literature"],
        )
        assert captured_kwargs.get("fields_of_study") is None
        assert candidates
        assert candidates[0].metadata.get("fields_of_study") == ["Environmental Science"]

    @pytest.mark.asyncio
    async def test_excluded_fields_in_metadata(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await BeyondYourFieldStrategy().get_candidates(context)

        assert candidates[0].metadata["excluded_fields"] == ["major", "minor"]

    @pytest.mark.asyncio
    async def test_user_major_in_metadata(self) -> None:
        context = _make_context(major="Biology")
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await BeyondYourFieldStrategy().get_candidates(context)

        assert candidates[0].metadata["user_major"] == "Biology"

    @pytest.mark.asyncio
    async def test_fallback_when_no_interests_or_keywords(self) -> None:
        """User with only major/minor should get fallback terms."""
        context = _make_context(
            interests=[],
            extracted_keywords={
                "major": ["Computer Science"],
                "minor": ["Mathematics"],
            },
        )
        captured_query = None

        async def mock_search(query, **kwargs):
            nonlocal captured_query
            captured_query = query
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            candidates = await BeyondYourFieldStrategy().get_candidates(context)

        assert captured_query is not None
        assert candidates[0].metadata["used_fallback"] is True

    @pytest.mark.asyncio
    async def test_not_random_generation(self) -> None:
        """Same context should produce same query (deterministic)."""
        context = _make_context()
        queries: list[str] = []

        async def mock_search(query, **kwargs):
            queries.append(query)
            return _make_ss_response(_make_paper())

        with patch(_SS_MOCK_PATH, side_effect=mock_search):
            await BeyondYourFieldStrategy().get_candidates(context)
            await BeyondYourFieldStrategy().get_candidates(context)

        assert len(queries) == 2
        assert queries[0] == queries[1]  # deterministic

    @pytest.mark.asyncio
    async def test_spotlight_type_set(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_make_ss_response(_make_paper())):
            candidates = await BeyondYourFieldStrategy().get_candidates(context)

        assert all(
            c.spotlight_type == SpotlightType.beyond_your_field for c in candidates
        )

    @pytest.mark.asyncio
    async def test_ss_failure_returns_empty(self) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_empty_ss_response()):
            candidates = await BeyondYourFieldStrategy().get_candidates(context)

        assert candidates == []


# ===================================================================
# COMMON CANDIDATE OUTPUT
# ===================================================================


class TestCommonCandidateOutput:
    """Cross-cutting checks that apply to all four implemented strategies."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "strategy_cls",
        [
            CountryPerspectiveStrategy,
            InfluentialResearchStrategy,
            LatestResearchStrategy,
            BeyondYourFieldStrategy,
        ],
    )
    async def test_returns_spotlight_candidate_model(
        self,
        strategy_cls: type,
    ) -> None:
        context = _make_context()
        with patch(
            _SS_MOCK_PATH,
            return_value=_make_ss_response(_make_paper(citation_count=100)),
        ):
            candidates = await strategy_cls().get_candidates(context)

        assert all(isinstance(c, SpotlightCandidate) for c in candidates)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "strategy_cls",
        [
            CountryPerspectiveStrategy,
            InfluentialResearchStrategy,
            LatestResearchStrategy,
            BeyondYourFieldStrategy,
        ],
    )
    async def test_query_is_non_empty_on_candidates(
        self,
        strategy_cls: type,
    ) -> None:
        context = _make_context()
        with patch(
            _SS_MOCK_PATH,
            return_value=_make_ss_response(_make_paper(citation_count=100)),
        ):
            candidates = await strategy_cls().get_candidates(context)

        assert all(c.query for c in candidates)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "strategy_cls",
        [
            CountryPerspectiveStrategy,
            InfluentialResearchStrategy,
            LatestResearchStrategy,
            BeyondYourFieldStrategy,
        ],
    )
    async def test_ss_failure_graceful_degradation(
        self,
        strategy_cls: type,
    ) -> None:
        context = _make_context()
        with patch(_SS_MOCK_PATH, return_value=_empty_ss_response()):
            candidates = await strategy_cls().get_candidates(context)

        assert candidates == []
