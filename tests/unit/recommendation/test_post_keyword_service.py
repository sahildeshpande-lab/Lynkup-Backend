from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from apps.recommendation.services.post_keyword_service import (
    build_post_plain_text,
    build_post_recommendation_payload,
    log_post_keywords_best_effort,
    persist_post_extraction,
)


def test_build_post_plain_text_combines_caption_and_html() -> None:
    text = build_post_plain_text(
        {
            "caption": "Hello #AI",
            "content_html": "<p>Learning <strong>FastAPI</strong></p>",
        }
    )
    assert "Hello #AI" in text
    assert "Learning FastAPI" in text


@pytest.mark.asyncio
async def test_build_post_recommendation_payload_merges_keyword_scores(
    mock_db,
) -> None:
    user_id = uuid4()
    profile = SimpleNamespace(
        major="Artificial Intelligence",
        minor="Data Science",
        profile_interests_id=[1],
    )

    extraction_path = Path(__file__).resolve().parents[2] / "tmp_test_extractions" / f"extraction-{uuid4()}.json"
    extraction_path.parent.mkdir(parents=True, exist_ok=True)
    extraction_path.write_text(
        json.dumps(
            {
                "user_id": str(user_id),
                "content_keywords": {"existing keyword": 2},
                "hashtags": {"ai": 3},
            }
        ),
        encoding="utf-8",
    )

    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: profile),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["Machine Learning"])),
        ]
    )

    fake_result = {
        "hashtags": ["ignored"],
        "keywords": ["existing keyword", "vector database"],
    }

    try:
        with patch(
            "apps.recommendation.services.post_keyword_service._extract_keywords_sync",
            return_value=fake_result,
        ):
            payload = await build_post_recommendation_payload(
                db,
                user_id=user_id,
                content={"caption": "Learning #FastAPI"},
                extraction_path=extraction_path,
            )

        assert payload == {
            "major": ["Artificial Intelligence"],
            "minor": ["Data Science"],
            "interests": ["Machine Learning"],
            "hashtags": {"ai": 3, "fastapi": 1},
            "engagement_keywords": {},
            "content_keywords": {"existing keyword": 3, "vector database": 1},
        }
    finally:
        extraction_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_persist_post_extraction_writes_single_object_with_updated_at() -> None:
    base = Path(__file__).resolve().parents[2] / "tmp_test_extractions"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"extraction-{uuid4()}.json"
    first_post_id = uuid4()
    second_post_id = uuid4()
    user_id = uuid4()
    payload = {
        "major": ["AI"],
        "minor": ["Ds"],
        "interests": ["Machine Learning"],
        "hashtags": {"fastapi": 1},
        "engagement_keywords": {},
        "content_keywords": {"rag": 1},
    }

    try:
        first_record = await persist_post_extraction(
            first_post_id,
            user_id=user_id,
            payload=payload,
            path=path,
        )

        stored = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(stored, dict)
        assert stored == first_record
        assert stored["latest_post_id"] == str(first_post_id)
        assert stored["post_ids"] == [str(first_post_id)]
        assert stored["updated_at"]
        assert stored["minor"] == ["Ds"]
        assert stored["interests"] == ["Machine Learning"]

        updated_payload = {
            **payload,
            "content_keywords": {"rag": 2, "nlp": 1},
            "hashtags": {"fastapi": 2},
        }
        second_record = await persist_post_extraction(
            second_post_id,
            user_id=user_id,
            payload=updated_payload,
            path=path,
        )

        stored = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(stored, dict)
        assert stored == second_record
        assert stored["latest_post_id"] == str(second_post_id)
        assert stored["post_ids"] == [str(first_post_id), str(second_post_id)]
        assert stored["content_keywords"] == {"rag": 2, "nlp": 1}
        assert stored["hashtags"] == {"fastapi": 2}
        assert stored["updated_at"]
    finally:
        if path.exists():
            path.unlink()


@pytest.mark.asyncio
async def test_persist_post_extraction_replaces_other_user_record() -> None:
    base = Path(__file__).resolve().parents[2] / "tmp_test_extractions"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"extraction-{uuid4()}.json"
    first_user_id = uuid4()
    second_user_id = uuid4()
    payload = {
        "major": ["AI"],
        "minor": [],
        "interests": [],
        "hashtags": {"ai": 1},
        "engagement_keywords": {},
        "content_keywords": {"rag": 1},
    }

    try:
        await persist_post_extraction(uuid4(), user_id=first_user_id, payload=payload, path=path)
        await persist_post_extraction(uuid4(), user_id=second_user_id, payload=payload, path=path)

        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["user_id"] == str(second_user_id)
        assert "updated_at" in stored
    finally:
        if path.exists():
            path.unlink()


@pytest.mark.asyncio
async def test_log_post_keywords_best_effort_prints_json(monkeypatch, mock_db) -> None:
    post_id = uuid4()
    user_id = uuid4()
    printed: list[str] = []
    extraction_path = Path(__file__).resolve().parents[2] / "tmp_test_extractions" / f"extraction-{uuid4()}.json"
    extraction_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(a) for a in args)))
    monkeypatch.setattr(
        "apps.recommendation.services.post_keyword_service.EXTRACTION_JSON_PATH",
        extraction_path,
    )

    payload = {
        "major": ["Artificial Intelligence"],
        "minor": ["Data Science"],
        "interests": ["Machine Learning"],
        "hashtags": {"fastapi": 2},
        "engagement_keywords": {},
        "content_keywords": {"rag": 4},
    }

    with patch(
        "apps.recommendation.services.post_keyword_service.build_post_recommendation_payload",
        AsyncMock(return_value=payload),
    ):
        await log_post_keywords_best_effort(
            post_id,
            {"caption": "Hello #FastAPI"},
            user_id=user_id,
            db=mock_db(),
        )

    assert any(str(post_id) in line for line in printed)
    assert any("Artificial Intelligence" in line for line in printed)
    assert any('"updated_at"' in line for line in printed)

    stored = json.loads(extraction_path.read_text(encoding="utf-8"))
    assert isinstance(stored, dict)
    assert stored["latest_post_id"] == str(post_id)
    assert stored["hashtags"] == {"fastapi": 2}
    extraction_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_log_post_keywords_best_effort_prints_profile_only_when_content_empty(
    monkeypatch,
    mock_db,
) -> None:
    printed: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(a) for a in args)))

    profile = SimpleNamespace(
        major="AI",
        minor=None,
        profile_interests_id=[],
    )
    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: profile),
        ]
    )

    await log_post_keywords_best_effort(uuid4(), {}, user_id=uuid4(), db=db)

    combined = "\n".join(printed)
    assert '"major": [' in combined
    assert '"minor": []' in combined
    assert '"interests": []' in combined
    assert '"hashtags": {}' in combined
    assert '"engagement_keywords": {}' in combined
    assert '"content_keywords": {}' in combined
    assert '"updated_at"' in combined


@pytest.mark.asyncio
async def test_log_post_keywords_best_effort_does_not_raise_on_failure(mock_db) -> None:
    with patch(
        "apps.recommendation.services.post_keyword_service.build_post_recommendation_payload",
        AsyncMock(side_effect=RuntimeError("model load failed")),
    ):
        await log_post_keywords_best_effort(
            uuid4(),
            {"caption": "hello"},
            user_id=uuid4(),
            db=mock_db(),
        )
