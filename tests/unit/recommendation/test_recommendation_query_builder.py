from __future__ import annotations

from apps.recommendation.services.recommendation_query_builder import (
    build_semantic_scholar_query,
    collect_topics,
    MAX_QUERY_TOPICS,
)


def test_only_major() -> None:
    extracted = {
        "major": ["Artificial Intelligence", "Machine Learning"],
        "minor": None,
        "interests": [],
        "hashtags": {},
        "engagement_keywords": {},
        "content_keywords": {},
    }
    assert collect_topics(extracted) == ["Artificial Intelligence", "Machine Learning"]
    assert (
        build_semantic_scholar_query(extracted)
        == "(\"Artificial Intelligence\"|\"Machine Learning\")"
    )


def test_major_plus_minor() -> None:
    extracted = {
        "major": ["Artificial Intelligence", "Machine Learning"],
        "minor": ["Data Science", "Deep Learning", "Extra"],
        "interests": [],
        "hashtags": {},
        "engagement_keywords": {},
        "content_keywords": {},
    }

    # Fill remaining slots from minor until MAX_QUERY_TOPICS.
    assert collect_topics(extracted) == [
        "Artificial Intelligence",
        "Machine Learning",
        "Data Science",
        "Deep Learning",
        "Extra",
    ]


def test_no_major_uses_minor_then_interests() -> None:
    extracted = {
        "minor": ["Data Science", "Deep Learning"],
        "interests": ["Machine Learning", "Artificial Intelligence"],
        "hashtags": {},
        "engagement_keywords": {},
        "content_keywords": {},
    }

    assert collect_topics(extracted) == [
        "Data Science",
        "Deep Learning",
        "Machine Learning",
        "Artificial Intelligence",
    ]


def test_no_minor_uses_major_then_interests() -> None:
    extracted = {
        "major": ["Artificial Intelligence", "Data Science"],
        "interests": ["Machine Learning", "Deep Learning"],
        "hashtags": {},
        "engagement_keywords": {},
        "content_keywords": {},
    }

    assert collect_topics(extracted) == [
        "Artificial Intelligence",
        "Data Science",
        "Machine Learning",
        "Deep Learning",
    ]


def test_empty_hashtags() -> None:
    extracted = {
        "major": ["Artificial Intelligence", "Machine Learning", "Deep Learning"],
        "minor": ["Data Science", "NLP"],
        "interests": [],
        "hashtags": {},
        "engagement_keywords": {"RAG": 20},
        "content_keywords": {"Semantic Search": 10},
    }

    # MAX_QUERY_TOPICS will be filled before hashtags are considered.
    assert len(collect_topics(extracted)) == MAX_QUERY_TOPICS


def test_empty_engagement_keywords() -> None:
    extracted = {
        "major": ["Artificial Intelligence"],
        "minor": ["Data Science"],
        "interests": ["Machine Learning"],
        "hashtags": {"python": 1},
        "engagement_keywords": {},
        "content_keywords": {"Semantic Search": 10, "Vector Database": 5},
    }

    # Engagement empty, so content keywords then hashtags.
    assert collect_topics(extracted) == [
        "Artificial Intelligence",
        "Data Science",
        "Machine Learning",
        "Semantic Search",
        "Vector Database",
    ]


def test_duplicate_topics_case_insensitive_dedupes() -> None:
    extracted = {
        "major": ["AI", "ai", "Ai", "Machine Learning"],
        "minor": [],
        "interests": [],
        "hashtags": {},
        "engagement_keywords": {},
        "content_keywords": {},
    }

    topics = collect_topics(extracted)
    assert topics[0] == "AI"
    assert "ai" not in [t.casefold() for t in topics[1:]]
    assert topics == ["AI", "Machine Learning"]


def test_more_than_max_query_topics_stops_immediately() -> None:
    extracted = {
        "major": ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"],
        "minor": ["Should", "Not", "Be", "Used"],
        "interests": [],
        "hashtags": {},
        "engagement_keywords": {},
        "content_keywords": {},
    }

    assert collect_topics(extracted) == ["T1", "T2", "T3", "T4", "T5"]


def test_fewer_than_max_query_topics() -> None:
    extracted = {
        "major": ["Artificial Intelligence"],
        "minor": ["Data Science"],
        "interests": ["Machine Learning"],
        "hashtags": {},
        "engagement_keywords": {},
        "content_keywords": {},
    }

    assert collect_topics(extracted) == [
        "Artificial Intelligence",
        "Data Science",
        "Machine Learning",
    ]


def test_completely_empty_json() -> None:
    assert collect_topics({}) == []
    assert build_semantic_scholar_query({}) == ""


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
    assert collect_topics(extracted) == [
        "Artificial Intelligence",
        "Data Science",
        "Machine Learning",
        "Deep Learning",
        "RAG",
    ]

    expected_query = "(\"Artificial Intelligence\"|\"Data Science\"|\"Machine Learning\"|\"Deep Learning\"|\"RAG\")"
    assert build_semantic_scholar_query(extracted) == expected_query

