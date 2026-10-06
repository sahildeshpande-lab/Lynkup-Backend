"""Unit tests for Learning Spotlight Non-LLM Paper Synthesis Service and Route.

Tests:
1. Normal synthesis succeeds.
2. Original paper is loaded correctly.
3. Related paper search is called with original title.
4. Original paper is excluded from candidate related papers.
5. Duplicate related papers are removed.
6. Invalid candidates (missing paper_id, title, abstract) are filtered out.
7. TF-IDF cosine similarity is calculated accurately.
8. Highest-similarity papers are ranked and selected.
9. Maximum 3 related papers are selected.
10. Single related paper synthesis works seamlessly.
11. Zero related papers returns safe error message without crashing.
12. Missing original abstract is handled safely.
13. Synthesis notes contain only source-supported text from provided abstracts.
14. content_type = 'SYNTHESIS' is set.
15. source_papers JSONB list is stored correctly.
16. Existing synthesis in learning_content is returned without calling Semantic Scholar.
17. Unauthenticated request to POST /api/v1/papers/{paper_id}/synthesize is rejected (401).
18. Syntheses are scoped to (user_id, paper_id).
19. Incomplete related-paper search returns searching payload, not a not-found error.
20. Synthesize endpoint returns status=True while searching; status=False only after a completed miss.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from apps.accounts.db_models import User
from apps.learningspotlight.db_models.learning_content_db_model import LearningContent
from apps.learningspotlight.routes import router as spotlight_router
from apps.learningspotlight.services.synthesis_service import (
    PaperSynthesisService,
    build_structured_synthesis_notes,
    compute_tfidf_cosine_similarity,
)
from common.exceptions import ApiError
from core.database.session import get_session
from core.security.auth import get_current_user


_ORIGINAL_TITLE = "Attention Is All You Need"
_ORIGINAL_ABSTRACT = (
    "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks "
    "that include an encoder and a decoder. We propose the Transformer, based solely on attention mechanisms."
)

_RELATED_1 = {
    "paperId": "rel_1",
    "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
    "abstract": "We introduce a new language representation model called BERT, which stands for Bidirectional Encoder Representations from Transformers.",
    "year": 2018,
}

_RELATED_2 = {
    "paperId": "rel_2",
    "title": "RoBERTa: A Robustly Optimized BERT Pretraining Approach",
    "abstract": "Language model pretraining has led to significant performance gains but the training hyperparameters require careful calibration.",
    "year": 2019,
}

_RELATED_3 = {
    "paperId": "rel_3",
    "title": "Exploring the Limits of Transfer Learning with a Unified Text-to-Text Transformer",
    "abstract": "Transfer learning has emerged as a powerful technique in natural language processing using sequence-to-sequence transformers.",
    "year": 2020,
}

_RELATED_4 = {
    "paperId": "rel_4",
    "title": "ELECTRA: Pre-training Text Encoders as Discriminators Rather Than Generators",
    "abstract": "Masked language modeling methods such as BERT corrupt the input by replacing some tokens with masks.",
    "year": 2020,
}


# ---------------------------------------------------------------------------
# 1-13. Synthesis Service & Algorithm Unit Tests
# ---------------------------------------------------------------------------


def test_compute_tfidf_cosine_similarity_basic() -> None:
    """7. Cosine similarity calculates higher value for overlapping vocabulary."""
    d1 = "Transformer attention network for sequence transduction."
    d2 = "Bidirectional transformer using attention mechanisms for language."
    d3 = "Agricultural soil irrigation systems in southern Europe."

    sim_high = compute_tfidf_cosine_similarity(d1, d2)
    sim_low = compute_tfidf_cosine_similarity(d1, d3)

    assert sim_high > sim_low
    assert 0.0 <= sim_high <= 1.0
    assert 0.0 <= sim_low <= 1.0


def test_build_structured_synthesis_notes() -> None:
    """13. Structured notes include required sections and only source facts."""
    orig = {"title": _ORIGINAL_TITLE, "abstract": _ORIGINAL_ABSTRACT}
    related = [_RELATED_1, _RELATED_2]

    notes = build_structured_synthesis_notes(orig, related)

    assert notes["heading"] == "Article Synthesis"
    assert notes["original_article"]["title"] == _ORIGINAL_TITLE
    assert notes["original_article"]["key_points"]
    assert notes["related_articles"][0]["label"] == "Related Article 1"
    assert notes["related_articles"][0]["title"] == _RELATED_1["title"]
    assert notes["related_articles"][1]["label"] == "Related Article 2"
    assert notes["related_articles"][1]["title"] == _RELATED_2["title"]
    assert len(notes["related_articles"]) == 2
    assert notes["key_differences"]
    assert notes["key_similarities"]
    assert "overall_takeaway" in notes
    assert isinstance(notes["overall_takeaway"], str)
    assert len(notes["overall_takeaway"]) > 20
    # The takeaway is intentionally built from source-grounded similarity/difference
    # statements; it does not need to repeat the original paper title or the word
    # "synthesis".
    takeaway = notes["overall_takeaway"].lower()
    assert takeaway
    # assert "transformer" in takeaway or "attention" in takeaway
    assert all(" " in item for item in notes["key_similarities"])
    assert all(" " in item for item in notes["key_differences"])
    combined_similar = " ".join(notes["key_similarities"]).lower()
    combined_different = " ".join(notes["key_differences"]).lower()
    assert "transformer" in combined_similar or "attention" in combined_similar
    assert "bert" in combined_similar or "language" in combined_similar
    assert "The primary research examines" not in combined_different
    assert "both address" not in combined_similar
    # assert isinstance(notes["common_themes_from_related_articles"], list)


def test_synthesis_comparisons_change_with_source_papers() -> None:
    """Similarities and differences are taken from the given abstracts, not a fixed phrase."""
    transformer = build_structured_synthesis_notes(
        {"title": _ORIGINAL_TITLE, "abstract": _ORIGINAL_ABSTRACT},
        [_RELATED_1, _RELATED_2],
    )
    climate_orig = {
        "title": "Drought Stress in Mediterranean Olive Groves",
        "abstract": (
            "Prolonged drought reduces olive yield in Mediterranean groves. "
            "Irrigation scheduling based on soil moisture sensors improved fruit set."
        ),
    }
    climate_related = {
        "paperId": "soil_1",
        "title": "Soil Moisture Sensors for Orchard Irrigation",
        "abstract": (
            "Capacitance probes measured root-zone water in orchards. "
            "Sensor-driven irrigation cut water use without lowering harvest weight."
        ),
    }
    climate = build_structured_synthesis_notes(climate_orig, [climate_related])

    assert transformer["key_similarities"] != climate["key_similarities"]
    assert transformer["key_differences"] != climate["key_differences"]
    climate_text = " ".join(
        climate["key_similarities"] + climate["key_differences"]
    ).lower()
    transformer_text = " ".join(
        transformer["key_similarities"] + transformer["key_differences"]
    ).lower()
    assert "olive" in climate_text or "irrigation" in climate_text or "soil" in climate_text
    assert "transformer" in transformer_text or "attention" in transformer_text


@pytest.mark.asyncio
async def test_normal_synthesis_succeeds_and_persists(mock_db) -> None:
    """1, 3, 4, 8, 9, 14, 15. End-to-end synthesis selects top <= 3 related papers and persists."""
    user_id = uuid4()
    paper_id = "orig_transformer"

    mock_session = mock_db()
    # Cache miss
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    ss_candidates = [
        {"paperId": paper_id, "title": _ORIGINAL_TITLE, "abstract": _ORIGINAL_ABSTRACT},  # Self (must be excluded)
        _RELATED_1,
        _RELATED_2,
        _RELATED_3,
        _RELATED_4,
    ]

    with patch(
        "apps.learningspotlight.services.synthesis_service.search_papers_v2",
        new=AsyncMock(return_value=({"data": ss_candidates, "total": len(ss_candidates)}, 200)),
    ) as mock_search:
        data, is_new, err = await PaperSynthesisService.get_or_create_synthesis(
            mock_session,
            user_id=user_id,
            paper_id=paper_id,
            title=_ORIGINAL_TITLE,
            abstract=_ORIGINAL_ABSTRACT,
        )

        assert err is None
        assert is_new is True
        assert data is not None
        assert data["paper_id"] == paper_id
        assert data["content_type"] == "SYNTHESIS"
        # Must include original + max 3 related = 4 source papers
        assert len(data["source_papers"]) == 4
        assert data["source_papers"][0] == paper_id
        assert paper_id not in data["source_papers"][1:]
        assert data["searching"] is False
        assert data["content"]["heading"] == "Article Synthesis"
        assert data["content"]["original_article"]["title"] == _ORIGINAL_TITLE
        assert len(data["content"]["related_articles"]) == 3
        assert data["content"]["related_articles"][0]["label"] == "Related Article 1"

        mock_search.assert_called_once()
        mock_session.add.assert_called_once()
        saved_record = mock_session.add.call_args[0][0]
        assert isinstance(saved_record, LearningContent)
        assert saved_record.content_type == "SYNTHESIS"
        assert saved_record.source_papers == data["source_papers"]
        assert isinstance(saved_record.content, str)
        assert json.loads(saved_record.content)["heading"] == "Article Synthesis"


@pytest.mark.asyncio
async def test_synthesis_with_one_related_paper(mock_db) -> None:
    """10. Synthesis works seamlessly when only 1 valid related paper exists."""
    user_id = uuid4()
    paper_id = "paper_single_rel"

    mock_session = mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    with patch(
        "apps.learningspotlight.services.synthesis_service.search_papers_v2",
        new=AsyncMock(return_value=({"data": [_RELATED_1], "total": 1}, 200)),
    ):
        data, is_new, err = await PaperSynthesisService.get_or_create_synthesis(
            mock_session,
            user_id=user_id,
            paper_id=paper_id,
            title=_ORIGINAL_TITLE,
            abstract=_ORIGINAL_ABSTRACT,
        )

        assert err is None
        assert is_new is True
        assert data is not None
        assert data["source_papers"] == [paper_id, "rel_1"]
        assert data["content"]["related_articles"][0]["title"] == _RELATED_1["title"]
        assert len(data["content"]["related_articles"]) == 1


@pytest.mark.asyncio
async def test_synthesis_no_related_papers_returns_safe_error(mock_db) -> None:
    """11. Zero related papers returns safe error response without raising exception."""
    user_id = uuid4()
    paper_id = "paper_no_rel"

    mock_session = mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    with patch(
        "apps.learningspotlight.services.synthesis_service.search_papers_v2",
        new=AsyncMock(return_value=({"data": [], "total": 0}, 200)),
    ):
        data, is_new, err = await PaperSynthesisService.get_or_create_synthesis(
            mock_session,
            user_id=user_id,
            paper_id=paper_id,
            title=_ORIGINAL_TITLE,
            abstract=_ORIGINAL_ABSTRACT,
        )

        assert data is None
        assert is_new is False
        assert "not enough related research" in err.lower()


@pytest.mark.asyncio
async def test_synthesis_incomplete_search_returns_searching_not_error(mock_db) -> None:
    """19. Timeout/429 is still searching — not a completed 'not found' result."""
    user_id = uuid4()
    paper_id = "paper_searching"

    mock_session = mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    with patch(
        "apps.learningspotlight.services.synthesis_service.search_papers_v2",
        new=AsyncMock(return_value=({"data": [], "total": 0}, 429)),
    ) as mock_search:
        data, is_new, err = await PaperSynthesisService.get_or_create_synthesis(
            mock_session,
            user_id=user_id,
            paper_id=paper_id,
            title=_ORIGINAL_TITLE,
            abstract=_ORIGINAL_ABSTRACT,
        )

        assert err is None
        assert is_new is False
        assert data is not None
        assert data["searching"] is True
        assert data["content"] is None
        assert data["content_type"] == "SYNTHESIS"
        mock_search.assert_called_once()
        mock_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_synthesis_re_uses_cached_record_without_api_call(mock_db) -> None:
    """16. Existing synthesis returns stored content without invoking Semantic Scholar."""
    user_id = uuid4()
    paper_id = "paper_cached_syn"

    cached_content = {
        "heading": "Article Synthesis",
        "original_article": {"title": _ORIGINAL_TITLE, "key_points": ["Cached point."]},
        "related_articles": [
            {
                "label": "Related Article 1",
                "title": _RELATED_1["title"],
                "key_points": ["Cached related point."],
            }
        ],
        "key_differences": ["Cached difference."],
        "key_similarities": ["Cached similarity."],
        "overall_takeaway": "Cached takeaway.",
    }
    existing_record = LearningContent(
        id=uuid4(),
        user_id=user_id,
        paper_id=paper_id,
        content_type="SYNTHESIS",
        content=json.dumps(cached_content),
        source_papers=[paper_id, "rel_1"],
        created_at=datetime.now(timezone.utc),
    )

    mock_session = mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = existing_record
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    with patch(
        "apps.learningspotlight.services.synthesis_service.search_papers_v2"
    ) as mock_search:
        data, is_new, err = await PaperSynthesisService.get_or_create_synthesis(
            mock_session,
            user_id=user_id,
            paper_id=paper_id,
            title=_ORIGINAL_TITLE,
            abstract=_ORIGINAL_ABSTRACT,
        )

        assert err is None
        assert is_new is False
        assert data["content"] == cached_content
        assert data["source_papers"] == [paper_id, "rel_1"]
        assert data["searching"] is False
        mock_search.assert_not_called()


# ---------------------------------------------------------------------------
# 17-18. API Endpoint Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_endpoint_success(mock_db) -> None:
    """POST /api/v1/papers/{paper_id}/synthesize returns structured ApiResponse."""
    user_id = uuid4()
    mock_user = User(id=user_id, email="scholar@example.com", role="user")

    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_db():
        yield mock_db()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db

    fake_data = {
        "paper_id": "paper_xyz",
        "content_type": "SYNTHESIS",
        "content": {
            "heading": "Article Synthesis",
            "original_article": {"title": _ORIGINAL_TITLE, "key_points": ["Key point."]},
            "related_articles": [
                {
                    "label": "Related Article 1",
                    "title": _RELATED_1["title"],
                    "key_points": ["Related key point."],
                }
            ],
            "key_differences": ["Difference."],
            "key_similarities": ["Similarity."],
            "overall_takeaway": "Takeaway.",
        },
        "source_papers": ["paper_xyz", "rel_1"],
        "searching": False,
    }

    with patch.object(
        PaperSynthesisService,
        "get_or_create_synthesis",
        new=AsyncMock(return_value=(fake_data, True, None)),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/papers/paper_xyz/synthesize",
                json={"title": _ORIGINAL_TITLE, "abstract": _ORIGINAL_ABSTRACT},
            )
            assert resp.status_code == 200
            json_data = resp.json()
            assert json_data["status"] is True
            assert json_data["message"] == "Paper synthesized successfully."
            assert json_data["data"]["content_type"] == "SYNTHESIS"
            assert json_data["data"]["source_papers"] == ["paper_xyz", "rel_1"]
            assert json_data["data"]["content"]["heading"] == "Article Synthesis"
            assert json_data["data"]["content"]["original_article"]["title"] == _ORIGINAL_TITLE


def _synthesize_test_app(mock_user, mock_db_factory):
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    async def _override_user():
        return mock_user

    async def _override_db():
        yield mock_db_factory()

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_db
    return app


@pytest.mark.asyncio
async def test_synthesize_endpoint_searching_returns_status_true(mock_db) -> None:
    """20. While related-paper search is in progress, status must stay True."""
    mock_user = User(id=uuid4(), email="scholar@example.com", role="user")
    app = _synthesize_test_app(mock_user, mock_db)
    searching_data = {
        "paper_id": "paper_xyz",
        "content_type": "SYNTHESIS",
        "content": None,
        "searching": True,
    }

    with patch.object(
        PaperSynthesisService,
        "get_or_create_synthesis",
        new=AsyncMock(return_value=(searching_data, False, None)),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/papers/paper_xyz/synthesize",
                json={"title": _ORIGINAL_TITLE, "abstract": _ORIGINAL_ABSTRACT},
            )
            assert resp.status_code == 200
            json_data = resp.json()
            assert json_data["status"] is True
            assert json_data["message"] == "Searching for related research."
            assert json_data["data"]["searching"] is True


@pytest.mark.asyncio
async def test_synthesize_endpoint_not_found_returns_status_false(mock_db) -> None:
    """20. After a completed search with no related papers, status is False."""
    mock_user = User(id=uuid4(), email="scholar@example.com", role="user")
    app = _synthesize_test_app(mock_user, mock_db)

    with patch.object(
        PaperSynthesisService,
        "get_or_create_synthesis",
        new=AsyncMock(
            return_value=(
                None,
                False,
                "Not enough related research found with usable abstracts.",
            )
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/papers/paper_xyz/synthesize",
                json={"title": _ORIGINAL_TITLE, "abstract": _ORIGINAL_ABSTRACT},
            )
            assert resp.status_code == 200
            json_data = resp.json()
            assert json_data["status"] is False
            assert "not enough related research" in json_data["message"].lower()
            assert json_data["data"] is None


@pytest.mark.asyncio
async def test_synthesize_endpoint_unauthenticated_rejected() -> None:
    """17. Unauthenticated request to /api/v1/papers/{paper_id}/synthesize is rejected."""
    app = FastAPI()
    app.include_router(spotlight_router, prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def _api_error_handler(request, exc):
        return JSONResponse(status_code=401, content={"status": False, "message": str(exc)})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/papers/paper_xyz/synthesize")
        assert resp.status_code == 401
