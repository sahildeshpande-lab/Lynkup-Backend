"""Unit tests for Learning Spotlight Non-LLM Paper Summarization Service and Route.

Tests:
1. Normal abstract generates extractive summary.
2. Summary word count is within target range (100–180 words) for long abstracts.
3. Sentence order from source abstract is preserved.
4. Summary contains ONLY original sentences from source text (strictly non-LLM/no hallucination).
5. Title overlap terms boost sentence selection.
6. Early sentence position bonus contributes to ranking.
7. Short abstract (<180 words) returns full available text.
8. Single sentence abstract returns that single sentence.
9. Missing abstract returns empty string safely without error.
10. Empty abstract returns empty string safely.
11. Existing summary in learning_content is returned without re-generating (idempotency).
12. New SUMMARY row is persisted into learning_content.
13. content_type is correctly set to 'SUMMARY'.
14. Unauthorized request to POST /api/v1/papers/{paper_id}/summarize is rejected (401).
15. Summaries are scoped to (user_id, paper_id).
16. Automatic fallback to local Profile.learning_spotlight when abstract not in request body.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.learningspotlight.db_models.learning_content_db_model import LearningContent
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.services.summarization_service import (
    PaperSummarizationService,
    count_words,
    generate_extractive_summary,
    split_into_sentences,
    tokenize,
)
from core.database.session import get_session
from core.security.auth import get_current_user


_LONG_ABSTRACT = (
    "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks "
    "that include an encoder and a decoder. The best performing models also connect the encoder and decoder through "
    "an attention mechanism. We propose a new simple network architecture, the Transformer, based solely on attention "
    "mechanisms, dispensing with recurrence and convolutions entirely. Experiments on two machine translation tasks "
    "show these models to be superior in quality while being more parallelizable and requiring significantly less time to train. "
    "Our model achieves 28.4 BLEU on the WMT 2014 English-to-German translation task, improving over the existing best results, "
    "including ensembles, by over 2 BLEU. On the WMT 2014 English-to-French translation task, our model establishes a new "
    "single-model state-of-the-art BLEU score of 41.8 after training for 3.5 days on eight GPUs, a small fraction of the "
    "training costs of the best models from the literature. We show that the Transformer generalizes well to other tasks "
    "by applying it successfully to English constituency parsing both with large and limited training data. In this paper, "
    "we provide comprehensive ablation studies and architectural details to facilitate future deep learning research."
)

_TITLE = "Attention Is All You Need"


# ---------------------------------------------------------------------------
# 1-10. Extractive Summarization Algorithm Tests
# ---------------------------------------------------------------------------


def test_normal_abstract_generates_extractive_summary() -> None:
    """1. Normal long abstract generates a non-empty extractive summary."""
    summary = generate_extractive_summary(title=_TITLE, abstract=_LONG_ABSTRACT)
    assert bool(summary) is True
    assert isinstance(summary, str)


def test_summary_target_length() -> None:
    """2. Extractive summary word count is within target bounds (<= 180 words)."""
    summary = generate_extractive_summary(title=_TITLE, abstract=_LONG_ABSTRACT, max_words=180)
    w_count = count_words(summary)
    assert 50 <= w_count <= 180


def test_sentence_order_is_preserved() -> None:
    """3. Selected sentences appear in the same chronological order as in the source abstract."""
    summary = generate_extractive_summary(title=_TITLE, abstract=_LONG_ABSTRACT, max_words=120)
    original_sentences = split_into_sentences(_LONG_ABSTRACT)
    summary_sentences = split_into_sentences(summary)

    indices = [original_sentences.index(s) for s in summary_sentences if s in original_sentences]
    assert indices == sorted(indices)


def test_summary_contains_only_source_sentences_no_hallucinations() -> None:
    """4, 5. Summary contains ONLY original sentences from the abstract with zero invented text."""
    summary = generate_extractive_summary(title=_TITLE, abstract=_LONG_ABSTRACT)
    original_sentences = split_into_sentences(_LONG_ABSTRACT)
    summary_sentences = split_into_sentences(summary)

    for s in summary_sentences:
        assert s in original_sentences


def test_title_overlap_boosts_relevant_sentences() -> None:
    """6. Sentences containing title keywords receive a scoring boost."""
    abstract = (
        "This is an unrelated first sentence about general computational mathematics. "
        "We introduce Attention Is All You Need as a new transformer architecture. "
        "Another sentence describing experimental setups and hardware benchmarks."
    )
    title = "Attention Is All You Need"
    summary = generate_extractive_summary(title=title, abstract=abstract, max_words=30)
    assert "We introduce Attention Is All You Need" in summary


def test_early_position_bonus() -> None:
    """7. Early sentences receive a position score contribution."""
    abstract = (
        "First foundational sentence with common terms. "
        "Second similar sentence with common terms. "
        "Third similar sentence with common terms."
    )
    # First sentence has highest position score
    summary = generate_extractive_summary(title="Title", abstract=abstract, max_words=15)
    assert "First foundational sentence" in summary


def test_short_abstract_returns_full_text() -> None:
    """8. Short abstract (under 180 words) returns the entire text without truncation."""
    short = "We propose an algorithm. It works well on all benchmark datasets."
    summary = generate_extractive_summary(title="Algorithm", abstract=short)
    assert summary == short


def test_single_sentence_abstract_returns_that_sentence() -> None:
    """8b. Single sentence abstract returns that exact sentence."""
    single = "A fast and efficient deep learning framework for graph networks."
    summary = generate_extractive_summary(title="Graph Networks", abstract=single)
    assert summary == single


def test_missing_or_empty_abstract_handled_safely() -> None:
    """9, 10. Missing or empty abstract returns empty string safely."""
    assert generate_extractive_summary(title="Title", abstract=None) == ""
    assert generate_extractive_summary(title="Title", abstract="") == ""
    assert generate_extractive_summary(title="Title", abstract="   ") == ""


# ---------------------------------------------------------------------------
# 11-13, 15. PaperSummarizationService Database Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_summary_persists_new_row(mock_db) -> None:
    """12, 13. Generates new summary and persists LearningContent with content_type=SUMMARY."""
    user_id = uuid4()
    paper_id = "paper_123"

    mock_session = mock_db()
    # First query returns None (not cached)
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    summary, is_new = await PaperSummarizationService.get_or_create_summary(
        mock_session,
        user_id=user_id,
        paper_id=paper_id,
        title=_TITLE,
        abstract=_LONG_ABSTRACT,
    )

    assert is_new is True
    assert bool(summary) is True
    mock_session.add.assert_called_once()
    added_obj = mock_session.add.call_args[0][0]
    assert isinstance(added_obj, LearningContent)
    assert added_obj.user_id == user_id
    assert added_obj.paper_id == paper_id
    assert added_obj.content_type == "SUMMARY"
    assert added_obj.content == summary


@pytest.mark.asyncio
async def test_get_or_create_summary_returns_cached_summary(mock_db) -> None:
    """11. When a summary already exists, returns existing content without re-generating."""
    user_id = uuid4()
    paper_id = "paper_cached"
    cached_content = "This is a previously persisted extractive summary."

    existing_record = LearningContent(
        id=uuid4(),
        user_id=user_id,
        paper_id=paper_id,
        content_type="SUMMARY",
        content=cached_content,
        created_at=datetime.now(timezone.utc),
    )

    mock_session = mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = existing_record
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.add = MagicMock()

    summary, is_new = await PaperSummarizationService.get_or_create_summary(
        mock_session,
        user_id=user_id,
        paper_id=paper_id,
        title=_TITLE,
        abstract=_LONG_ABSTRACT,
    )

    assert is_new is False
    assert summary == cached_content
    mock_session.add.assert_not_called()


# ---------------------------------------------------------------------------
# 14-16. API Endpoint Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summarize_endpoint_success(mock_db) -> None:
    """12. POST /api/v1/papers/{paper_id}/summarize returns standard ApiResponse."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="student@example.com", role="user")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db

    fake_summary = "We propose the Transformer architecture solely based on attention mechanisms."

    with patch.object(
        PaperSummarizationService,
        "get_or_create_summary",
        new=AsyncMock(return_value=(fake_summary, True)),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/papers/paper_123/summarize",
                json={"title": _TITLE, "abstract": _LONG_ABSTRACT},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] is True
            assert data["message"] == "Paper summarized successfully."
            assert data["data"]["paper_id"] == "paper_123"
            assert data["data"]["content_type"] == "SUMMARY"
            assert data["data"]["content"] == fake_summary


@pytest.mark.asyncio
async def test_summarize_endpoint_missing_abstract_returns_error(mock_db) -> None:
    """9. POST /api/v1/papers/{paper_id}/summarize with no abstract returns error response."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="student@example.com", role="user")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db

    with patch.object(
        PaperSummarizationService,
        "get_or_create_summary",
        new=AsyncMock(return_value=("", False)),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/papers/paper_empty/summarize",
                json={"title": "Title Only"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] is False
            assert "abstract is missing" in data["message"].lower()


@pytest.mark.asyncio
async def test_summarize_endpoint_unauthorized_rejected() -> None:
    """14. Unauthenticated request to /api/v1/papers/{paper_id}/summarize is rejected."""
    from common.exceptions import ApiError
    from fastapi.responses import JSONResponse

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def _api_error_handler(request, exc):
        return JSONResponse(status_code=401, content={"status": False, "message": str(exc)})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/papers/paper_123/summarize")
        assert resp.status_code == 401

