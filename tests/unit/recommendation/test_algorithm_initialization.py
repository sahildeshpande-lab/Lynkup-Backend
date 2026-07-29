from __future__ import annotations

import pytest

from apps.recommendation.services import algorithm


@pytest.fixture(autouse=True)
def _reset_models(monkeypatch) -> None:
    monkeypatch.setattr(algorithm, "nlp", None, raising=False)
    monkeypatch.setattr(algorithm, "kw_model", None, raising=False)


def test_preprocess_text_requires_initialization() -> None:
    with pytest.raises(RuntimeError, match="Recommendation models are not initialized"):
        algorithm.preprocess_text("machine learning")


def test_extract_keywords_requires_initialization() -> None:
    with pytest.raises(RuntimeError, match="Recommendation models are not initialized"):
        algorithm.extract_keywords("machine learning")


def test_initialize_models_is_idempotent(monkeypatch) -> None:
    fake_nlp = object()
    load_calls = {"count": 0}

    def fake_load(_name: str) -> object:
        load_calls["count"] += 1
        return fake_nlp

    fake_spacy = type("FakeSpacy", (), {"load": staticmethod(fake_load)})()

    class FakeSentenceTransformer:
        init_count = 0

        def __init__(self, _model_name: str) -> None:
            FakeSentenceTransformer.init_count += 1

    class FakeKeyBERT:
        init_count = 0

        def __init__(self, *, model=None, **kwargs) -> None:
            FakeKeyBERT.init_count += 1
            self.model = model

    monkeypatch.setitem(__import__("sys").modules, "spacy", fake_spacy)
    monkeypatch.setitem(
        __import__("sys").modules,
        "sentence_transformers",
        type(
            "FakeSentenceTransformersModule",
            (),
            {"SentenceTransformer": FakeSentenceTransformer},
        )(),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "keybert",
        type("FakeKeybertModule", (), {"KeyBERT": FakeKeyBERT})(),
    )

    algorithm.initialize_models()
    algorithm.initialize_models()

    assert load_calls["count"] == 1
    assert FakeSentenceTransformer.init_count == 1
    assert FakeKeyBERT.init_count == 1
    assert algorithm.nlp is fake_nlp
    assert algorithm.kw_model is not None
