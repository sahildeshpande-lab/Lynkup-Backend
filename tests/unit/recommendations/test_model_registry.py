from __future__ import annotations

import pytest

from apps.recommendations.services import model_registry


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        model_registry,
        "_registry",
        model_registry.RecommendationModelRegistry(),
    )


def test_registry_initialize_is_idempotent(monkeypatch) -> None:
    spacy_calls = {"count": 0}
    keybert_calls = {"count": 0}

    fake_spacy = type(
        "FakeSpacy",
        (),
        {"load": staticmethod(lambda _name: (spacy_calls.__setitem__("count", spacy_calls["count"] + 1) or object()))},
    )()

    class FakeKeyBERT:
        def __init__(self, *, model=None, **kwargs) -> None:
            keybert_calls["count"] += 1

    monkeypatch.setitem(__import__("sys").modules, "spacy", fake_spacy)
    monkeypatch.setitem(
        __import__("sys").modules,
        "keybert",
        type("FakeKeybertModule", (), {"KeyBERT": FakeKeyBERT})(),
    )

    registry = model_registry.get_registry()
    registry.initialize()
    registry.initialize()

    assert spacy_calls["count"] == 1
    assert keybert_calls["count"] == 1
    assert registry.is_ready


def test_registry_raises_before_initialization() -> None:
    registry = model_registry.get_registry()

    with pytest.raises(RuntimeError, match="spaCy model is not initialized"):
        _ = registry.nlp

    with pytest.raises(RuntimeError, match="KeyBERT model is not initialized"):
        _ = registry.kw_model
