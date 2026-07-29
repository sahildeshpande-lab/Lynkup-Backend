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


def test_extract_keywords_requires_spacy_initialization() -> None:
    with pytest.raises(RuntimeError, match="Recommendation models are not initialized"):
        algorithm.extract_keywords("machine learning")


def test_initialize_models_only_loads_spacy(monkeypatch) -> None:
    fake_nlp = object()
    load_calls = {"count": 0}

    def fake_load(_name: str) -> object:
        load_calls["count"] += 1
        return fake_nlp

    fake_spacy = type("FakeSpacy", (), {"load": staticmethod(fake_load)})()
    monkeypatch.setitem(__import__("sys").modules, "spacy", fake_spacy)

    algorithm.initialize_models()
    algorithm.initialize_models()

    assert load_calls["count"] == 1
    assert algorithm.nlp is fake_nlp
    assert algorithm.kw_model is None


def test_get_keyword_model_lazy_loads_and_caches(monkeypatch) -> None:
    class FakeKeyBERT:
        init_count = 0

        def __init__(self, *, model=None, **kwargs) -> None:
            FakeKeyBERT.init_count += 1
            self.model = model

        def extract_keywords(self, *_args, **_kwargs):
            return [("keyword", 0.9)]

    monkeypatch.setitem(
        __import__("sys").modules,
        "keybert",
        type("FakeKeybertModule", (), {"KeyBERT": FakeKeyBERT})(),
    )

    first = algorithm.get_keyword_model()
    second = algorithm.get_keyword_model()

    assert FakeKeyBERT.init_count == 1
    assert first is second
    assert algorithm.kw_model is first
