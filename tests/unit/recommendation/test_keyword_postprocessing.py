from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.recommendation.services.keyword_postprocessing import (
    clean_keywords,
    filter_by_pos,
    filter_by_score,
    normalize_keyword,
    remove_blacklisted_keywords,
    remove_subphrase_duplicates,
    sort_keywords,
)


def _token(text: str, *, lemma: str | None = None, pos: str = "NOUN") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        lemma_=lemma or text.lower(),
        pos_=pos,
        is_space=False,
    )


def _space() -> SimpleNamespace:
    return SimpleNamespace(is_space=True)


def test_filter_by_score_discards_low_scoring_keywords() -> None:
    keywords = [
        ("machine learning", 0.82),
        ("great", 0.21),
        ("semantic search", 0.78),
    ]

    filtered = filter_by_score(keywords, min_score=0.45)

    assert filtered == [
        ("machine learning", 0.82),
        ("semantic search", 0.78),
    ]


def test_normalize_keyword_collapses_whitespace_and_lemmatizes() -> None:
    fake_doc = [_token("Artificial"), _space(), _token("Intelligence")]

    with patch(
        "apps.recommendation.services.keyword_postprocessing._nlp_model",
        return_value=lambda text: fake_doc,
    ):
        assert normalize_keyword("Artificial   Intelligence") == "artificial intelligence"


def test_remove_blacklisted_keywords() -> None:
    keywords = [
        ("machine learning", 0.9),
        ("great", 0.8),
        ("semantic search", 0.7),
    ]

    filtered = remove_blacklisted_keywords(keywords)

    assert filtered == [
        ("machine learning", 0.9),
        ("semantic search", 0.7),
    ]


def test_filter_by_pos_rejects_verb_only_phrases() -> None:
    docs = {
        "machine learning": [_token("machine", pos="NOUN"), _token("learning", pos="NOUN")],
        "get know": [_token("get", pos="VERB"), _token("know", pos="VERB")],
        "semantic search": [_token("semantic", pos="ADJ"), _token("search", pos="NOUN")],
    }

    with patch(
        "apps.recommendation.services.keyword_postprocessing._nlp_model",
        return_value=lambda text: docs[text],
    ):
        filtered = filter_by_pos(
            [
                ("machine learning", 0.9),
                ("get know", 0.4),
                ("semantic search", 0.8),
            ]
        )

    assert filtered == [
        ("machine learning", 0.9),
        ("semantic search", 0.8),
    ]


def test_remove_subphrase_duplicates_prefers_longest_phrase() -> None:
    keywords = [
        ("machine", 0.95),
        ("machine learning", 0.89),
        ("semantic", 0.7),
        ("semantic search", 0.81),
    ]

    deduped = remove_subphrase_duplicates(keywords)

    assert ("machine learning", 0.89) in deduped
    assert ("semantic search", 0.81) in deduped
    assert ("machine", 0.95) not in deduped
    assert ("semantic", 0.7) not in deduped


def test_sort_keywords_orders_by_score_desc_then_name() -> None:
    keywords = [
        ("semantic search", 0.81),
        ("machine learning", 0.89),
        ("vector database", 0.69),
    ]

    assert sort_keywords(keywords) == [
        ("machine learning", 0.89),
        ("semantic search", 0.81),
        ("vector database", 0.69),
    ]


def test_clean_keywords_runs_full_pipeline() -> None:
    raw = [
        ("Machine Learning", 0.89),
        ("machine learning", 0.75),
        ("semantic search", 0.81),
        ("machine", 0.95),
        ("get know", 0.18),
        ("great", 0.21),
        ("supervised learning", 0.73),
    ]
    docs = {
        "machine learning": [_token("machine", pos="NOUN"), _token("learning", pos="NOUN")],
        "semantic search": [_token("semantic", pos="ADJ"), _token("search", pos="NOUN")],
        "machine": [_token("machine", pos="NOUN")],
        "get know": [_token("get", pos="VERB"), _token("know", pos="VERB")],
        "great": [_token("great", pos="ADJ")],
        "supervised learning": [_token("supervised", pos="ADJ"), _token("learning", pos="NOUN")],
    }

    def fake_nlp(text: str):
        return docs.get(text, [_token(text, pos="NOUN")])

    with patch(
        "apps.recommendation.services.keyword_postprocessing._nlp_model",
        return_value=fake_nlp,
    ):
        cleaned = clean_keywords(raw, min_score=0.45)

    assert cleaned == [
        ("machine learning", 0.89),
        ("semantic search", 0.81),
        ("supervised learning", 0.73),
    ]


def test_clean_keywords_keeps_highest_score_for_normalized_duplicates() -> None:
    raw = [
        ("Machine Learning", 0.82),
        ("MACHINE LEARNING", 0.91),
    ]

    with patch(
        "apps.recommendation.services.keyword_postprocessing._nlp_model",
        return_value=lambda text: [
            _token("machine", pos="NOUN"),
            _token("learning", pos="NOUN"),
        ],
    ):
        cleaned = clean_keywords(raw, min_score=0.45)

    assert cleaned == [("machine learning", 0.91)]
