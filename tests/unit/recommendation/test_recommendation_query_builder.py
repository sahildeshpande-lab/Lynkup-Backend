from __future__ import annotations

import urllib.parse

from apps.recommendation.services.recommendation_query_builder import (
    build_semantic_scholar_query,
    MAX_QUERY_TOPICS,
)


def _encoded(query: str) -> str:
    return urllib.parse.quote(query, safe="")


def test_only_major() -> None:
    result = build_semantic_scholar_query(
        {
            "major": ["Artificial Intelligence", "Machine Learning"],
            "minor": None,
            "interests": [],
            "hashtags": {},
            "engagement_keywords": {},
            "content_keywords": {},
        }
    )

    assert result["topics"] == ["Artificial Intelligence", "Machine Learning"]
    assert result["query"] == "(\"Artificial Intelligence\"|\"Machine Learning\")"
    assert result["encoded_query"] == _encoded(result["query"])


def test_major_plus_minor() -> None:
    result = build_semantic_scholar_query(
        {
            "major": ["Artificial Intelligence", "Machine Learning"],
            "minor": ["Data Science", "Deep Learning", "Extra"],
            "interests": [],
            "hashtags": {},
            "engagement_keywords": {},
            "content_keywords": {},
        }
    )

    # Fill remaining slots from minor until MAX_QUERY_TOPICS.
    assert result["topics"] == [
        "Artificial Intelligence",
        "Machine Learning",
        "Data Science",
        "Deep Learning",
        "Extra",
    ]


def test_no_major_uses_minor_then_interests() -> None:
    result = build_semantic_scholar_query(
        {
            "minor": ["Data Science", "Deep Learning"],
            "interests": ["Machine Learning", "Artificial Intelligence"],
            "hashtags": {},
            "engagement_keywords": {},
            "content_keywords": {},
        }
    )

    assert result["topics"] == [
        "Data Science",
        "Deep Learning",
        "Machine Learning",
        "Artificial Intelligence",
    ]


def test_no_minor_uses_major_then_interests() -> None:
    result = build_semantic_scholar_query(
        {
            "major": ["Artificial Intelligence", "Data Science"],
            "interests": ["Machine Learning", "Deep Learning"],
            "hashtags": {},
            "engagement_keywords": {},
            "content_keywords": {},
        }
    )

    assert result["topics"] == [
        "Artificial Intelligence",
        "Data Science",
        "Machine Learning",
        "Deep Learning",
    ]


def test_empty_hashtags() -> None:
    result = build_semantic_scholar_query(
        {
            "major": ["Artificial Intelligence", "Machine Learning", "Deep Learning"],
            "minor": ["Data Science", "NLP"],
            "interests": [],
            "hashtags": {},
            "engagement_keywords": {"RAG": 20},
            "content_keywords": {"Semantic Search": 10},
        }
    )

    # MAX_QUERY_TOPICS will be filled before hashtags are considered.
    assert len(result["topics"]) == MAX_QUERY_TOPICS


def test_empty_engagement_keywords() -> None:
    result = build_semantic_scholar_query(
        {
            "major": ["Artificial Intelligence"],
            "minor": ["Data Science"],
            "interests": ["Machine Learning"],
            "hashtags": {"python": 1},
            "engagement_keywords": {},
            "content_keywords": {"Semantic Search": 10, "Vector Database": 5},
        }
    )

    # Engagement empty, so content keywords then hashtags.
    assert result["topics"] == [
        "Artificial Intelligence",
        "Data Science",
        "Machine Learning",
        "Semantic Search",
        "Vector Database",
    ]


def test_duplicate_topics_case_insensitive_dedupes() -> None:
    result = build_semantic_scholar_query(
        {
            "major": ["AI", "ai", "Ai", "Machine Learning"],
            "minor": [],
            "interests": [],
            "hashtags": {},
            "engagement_keywords": {},
            "content_keywords": {},
        }
    )

    assert result["topics"][0] == "AI"
    assert "ai" not in [t.casefold() for t in result["topics"][1:]]
    assert result["topics"] == ["AI", "Machine Learning"]


def test_more_than_max_query_topics_stops_immediately() -> None:
    result = build_semantic_scholar_query(
        {
            "major": [
                "T1",
                "T2",
                "T3",
                "T4",
                "T5",
                "T6",
                "T7",
                "T8",
            ],
            "minor": ["Should", "Not", "Be", "Used"],
            "interests": [],
            "hashtags": {},
            "engagement_keywords": {},
            "content_keywords": {},
        }
    )

    assert result["topics"] == ["T1", "T2", "T3", "T4", "T5"]


def test_fewer_than_max_query_topics() -> None:
    result = build_semantic_scholar_query(
        {
            "major": ["Artificial Intelligence"],
            "minor": ["Data Science"],
            "interests": ["Machine Learning"],
            "hashtags": {},
            "engagement_keywords": {},
            "content_keywords": {},
        }
    )

    assert result["topics"] == [
        "Artificial Intelligence",
        "Data Science",
        "Machine Learning",
    ]


def test_completely_empty_json() -> None:
    result = build_semantic_scholar_query({})
    assert result["topics"] == []
    assert result["query"] == ""
    assert result["encoded_query"] == ""


def test_full_example_matches_expected_encoded_query() -> None:
    extracted = {
        "major": ["Artificial Intelligence"],
        "minor": ["Data Science"],
        "interests": ["Machine Learning", "Deep Learning"],
        "hashtags": {"python": 10, "fastapi": 5},
        "engagement_keywords": {"RAG": 20, "LLM": 18, "NLP": 15},
        "content_keywords": {"Semantic Search": 16, "Vector Database": 12, "Computer Vision": 5},
    }

    # MAX_QUERY_TOPICS=5 and priority means: major, minor, interests, then engagement top score terms.
    result = build_semantic_scholar_query(extracted)

    assert result["topics"] == [
        "Artificial Intelligence",
        "Data Science",
        "Machine Learning",
        "Deep Learning",
        "RAG",
    ]

    expected_query = "(\"Artificial Intelligence\"|\"Data Science\"|\"Machine Learning\"|\"Deep Learning\"|\"RAG\")"
    assert result["query"] == expected_query
    assert result["encoded_query"] == _encoded(expected_query)

