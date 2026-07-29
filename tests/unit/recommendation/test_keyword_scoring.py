from __future__ import annotations

import pytest

from apps.recommendation.services.keyword_scoring import (
    coerce_keyword_scores,
    get_top_keywords,
    subtract_keyword_scores,
    update_keyword_scores,
)


def test_update_keyword_scores_increments_existing_and_adds_new() -> None:
    existing = {"python": 3, "fastapi": 2}
    new_keywords = ["Python", "FastAPI", "Machine Learning", "Python"]

    result = update_keyword_scores(existing, new_keywords)

    assert result == {
        "python": 4,
        "fastapi": 3,
        "machine learning": 1,
    }
    assert existing == {"python": 3, "fastapi": 2}


def test_update_keyword_scores_ignores_empty_and_whitespace() -> None:
    result = update_keyword_scores({}, ["", "  ", "  RAG  "])

    assert result == {"rag": 1}


def test_update_keyword_scores_custom_increment() -> None:
    result = update_keyword_scores({"nlp": 5}, ["NLP", "LLM"], increment=3)

    assert result == {"nlp": 8, "llm": 3}


def test_subtract_keyword_scores_removes_and_reduces_scores() -> None:
    existing = {"python": 4, "fastapi": 2, "rag": 1}

    result = subtract_keyword_scores(
        existing,
        {"python": 3, "fastapi": 2, "missing": 1},
    )

    assert result == {"python": 1, "rag": 1}
    assert existing == {"python": 4, "fastapi": 2, "rag": 1}


def test_get_top_keywords_sorts_by_score_descending() -> None:
    scores = {
        "python": 12,
        "machine learning": 9,
        "semantic search": 6,
        "rag": 4,
    }

    assert get_top_keywords(scores, top_n=5) == [
        "python",
        "machine learning",
        "semantic search",
        "rag",
    ]


def test_get_top_keywords_respects_top_n_and_breaks_ties_alphabetically() -> None:
    scores = {"zebra": 5, "alpha": 5, "beta": 10}

    assert get_top_keywords(scores, top_n=2) == ["beta", "alpha"]
    assert get_top_keywords(scores, top_n=0) == []


def test_coerce_keyword_scores_from_list() -> None:
    assert coerce_keyword_scores(["Python", "python", "FastAPI"]) == {
        "python": 1,
        "fastapi": 1,
    }


def test_coerce_keyword_scores_from_dict() -> None:
    assert coerce_keyword_scores({"RAG": 4, "rag": 2, "": 9}) == {"rag": 6}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, {}),
        ("invalid", {}),
        ({}, {}),
    ],
)
def test_coerce_keyword_scores_handles_invalid_values(value: object, expected: dict[str, int]) -> None:
    assert coerce_keyword_scores(value) == expected
