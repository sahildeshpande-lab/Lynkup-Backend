from __future__ import annotations

import pytest

from apps.recommendations.services import algorithm, model_registry


@pytest.fixture(autouse=True)
def _reset_models(monkeypatch) -> None:
    monkeypatch.setattr(model_registry, "_registry", model_registry.RecommendationModelRegistry())
    monkeypatch.setattr(algorithm, "nlp", None, raising=False)
    monkeypatch.setattr(algorithm, "kw_model", None, raising=False)


def test_preprocess_text_requires_initialization() -> None:
    with pytest.raises(RuntimeError, match="Recommendation models are not initialized"):
        algorithm.preprocess_text("machine learning")


def test_extract_keywords_requires_initialization() -> None:
    with pytest.raises(RuntimeError, match="Recommendation models are not initialized"):
        algorithm.extract_keywords("machine learning")


def test_initialize_models_loads_spacy_and_keybert(monkeypatch) -> None:
    fake_nlp = object()
    spacy_load_calls = {"count": 0}
    keybert_init_calls = {"count": 0}

    def fake_spacy_load(_name: str) -> object:
        spacy_load_calls["count"] += 1
        return fake_nlp

    class FakeKeyBERT:
        def __init__(self, *, model=None, **kwargs) -> None:
            keybert_init_calls["count"] += 1
            self.model = model

    fake_spacy = type("FakeSpacy", (), {"load": staticmethod(fake_spacy_load)})()
    monkeypatch.setitem(__import__("sys").modules, "spacy", fake_spacy)
    monkeypatch.setitem(
        __import__("sys").modules,
        "keybert",
        type("FakeKeybertModule", (), {"KeyBERT": FakeKeyBERT})(),
    )

    algorithm.initialize_models()
    algorithm.initialize_models()

    assert spacy_load_calls["count"] == 1
    assert keybert_init_calls["count"] == 1
    assert algorithm.nlp is fake_nlp
    assert algorithm.kw_model is not None
    assert model_registry.get_registry().is_ready


def test_get_keyword_model_returns_cached_instance_after_startup(monkeypatch) -> None:
    class FakeKeyBERT:
        init_count = 0

        def __init__(self, *, model=None, **kwargs) -> None:
            FakeKeyBERT.init_count += 1
            self.model = model

        def extract_keywords(self, *_args, **_kwargs):
            return [("keyword", 0.9)]

    fake_spacy = type(
        "FakeSpacy",
        (),
        {"load": staticmethod(lambda _name: object())},
    )()
    monkeypatch.setitem(__import__("sys").modules, "spacy", fake_spacy)
    monkeypatch.setitem(
        __import__("sys").modules,
        "keybert",
        type("FakeKeybertModule", (), {"KeyBERT": FakeKeyBERT})(),
    )

    algorithm.initialize_models()
    first = algorithm.get_keyword_model()
    second = algorithm.get_keyword_model()

    assert FakeKeyBERT.init_count == 1
    assert first is second


def test_get_keyword_model_raises_when_not_initialized() -> None:
    with pytest.raises(RuntimeError, match="KeyBERT model is not initialized"):
        algorithm.get_keyword_model()
