from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.recommendation.services.post_keyword_service import (
    build_post_plain_text,
    build_post_recommendation_payload,
    log_post_keywords_best_effort,
    persist_profile_extracted_keywords,
    refresh_profile_extracted_keywords,
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
        extracted_keywords={
            "content_keywords": {"existing keyword": 2},
            "hashtags": {"ai": 3},
        },
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

    with patch(
        "apps.recommendation.services.post_keyword_service._extract_keywords_sync",
        return_value=fake_result,
    ):
        payload = await build_post_recommendation_payload(
            db,
            user_id=user_id,
            content={"caption": "Learning #FastAPI"},
        )

    assert payload == {
        "major": ["Artificial Intelligence"],
        "minor": ["Data Science"],
        "interests": ["Machine Learning"],
        "hashtags": {"ai": 3, "fastapi": 1},
        "engagement_keywords": {},
        "content_keywords": {"existing keyword": 3, "vector database": 1},
        "_post_snapshot": {
            "content_keywords": {"existing keyword": 1, "vector database": 1},
            "hashtags": {"fastapi": 1},
        },
    }


@pytest.mark.asyncio
async def test_build_post_recommendation_payload_replaces_previous_post_keywords(
    mock_db,
) -> None:
    user_id = uuid4()
    profile = SimpleNamespace(
        major="Computer Science",
        minor="Statistics",
        profile_interests_id=[1],
        extracted_keywords={
            "content_keywords": {"old topic": 1, "shared topic": 2},
            "hashtags": {"oldtag": 1, "sharedtag": 1},
        },
    )

    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: profile),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["AI"])),
        ]
    )

    fake_result = {
        "hashtags": ["ignored"],
        "keywords": ["new topic", "shared topic"],
    }

    with patch(
        "apps.recommendation.services.post_keyword_service._extract_keywords_sync",
        return_value=fake_result,
    ):
        payload = await build_post_recommendation_payload(
            db,
            user_id=user_id,
            content={"caption": "Updated #NewTag #SharedTag"},
            previous_post_snapshot={
                "content_keywords": {"old topic": 1, "shared topic": 1},
                "hashtags": {"oldtag": 1, "sharedtag": 1},
            },
        )

    assert payload["content_keywords"] == {
        "shared topic": 2,
        "new topic": 1,
    }
    assert payload["hashtags"] == {
        "newtag": 1,
        "sharedtag": 1,
    }
    assert payload["_post_snapshot"]["content_keywords"] == {
        "new topic": 1,
        "shared topic": 1,
    }


@pytest.mark.asyncio
async def test_refresh_profile_extracted_keywords_updates_major_minor_interests(
    mock_db,
) -> None:
    user_id = uuid4()
    profile = SimpleNamespace(
        user_id=user_id,
        major="Computer Science",
        minor="Data Science",
        profile_interests_id=[1],
        extracted_keywords={
            "content_keywords": {"rag": 4},
            "hashtags": {"ai": 2},
            "engagement_keywords": {"paper": 1},
            "post_ids": ["post-1"],
            "latest_post_id": "post-1",
        },
        keywords_updated_at=None,
    )

    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: profile),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["Machine Learning"])),
        ]
    )
    db.add = MagicMock()
    db.commit = AsyncMock()

    record = await refresh_profile_extracted_keywords(db, user_id=user_id)

    assert record["major"] == ["Computer Science"]
    assert record["minor"] == ["Data Science"]
    assert record["interests"] == ["Machine Learning"]
    assert record["content_keywords"] == {"rag": 4}
    assert record["hashtags"] == {"ai": 2}
    assert record["engagement_keywords"] == {"paper": 1}
    assert record["post_ids"] == ["post-1"]
    assert record["latest_post_id"] == "post-1"
    assert profile.keywords_updated_at is not None


@pytest.mark.asyncio
async def test_persist_profile_extracted_keywords_accumulates_post_ids(mock_db) -> None:
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

    profile = SimpleNamespace(
        user_id=user_id,
        extracted_keywords=None,
        keywords_updated_at=None,
    )
    db = mock_db()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: profile))
    db.add = MagicMock()
    db.commit = AsyncMock()

    first_record = await persist_profile_extracted_keywords(
        db,
        post_id=first_post_id,
        user_id=user_id,
        payload=payload,
    )

    assert first_record["latest_post_id"] == str(first_post_id)
    assert first_record["post_ids"] == [str(first_post_id)]
    assert first_record["minor"] == ["Ds"]
    assert profile.keywords_updated_at is not None

    profile.extracted_keywords = first_record
    updated_payload = {
        **payload,
        "content_keywords": {"rag": 2, "nlp": 1},
        "hashtags": {"fastapi": 2},
    }
    second_record = await persist_profile_extracted_keywords(
        db,
        post_id=second_post_id,
        user_id=user_id,
        payload=updated_payload,
    )

    assert second_record["latest_post_id"] == str(second_post_id)
    assert second_record["post_ids"] == [str(first_post_id), str(second_post_id)]
    assert second_record["content_keywords"] == {"rag": 2, "nlp": 1}
    assert second_record["hashtags"] == {"fastapi": 2}


@pytest.mark.asyncio
async def test_persist_profile_extracted_keywords_skips_missing_profile(mock_db) -> None:
    db = mock_db()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

    record = await persist_profile_extracted_keywords(
        db,
        post_id=uuid4(),
        user_id=uuid4(),
        payload={"major": [], "minor": [], "interests": [], "hashtags": {}, "engagement_keywords": {}, "content_keywords": {}},
    )

    assert record == {}
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_log_post_keywords_best_effort_prints_json(monkeypatch, mock_db) -> None:
    post_id = uuid4()
    user_id = uuid4()
    printed: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(a) for a in args)))

    payload = {
        "major": ["Artificial Intelligence"],
        "minor": ["Data Science"],
        "interests": ["Machine Learning"],
        "hashtags": {"fastapi": 2},
        "engagement_keywords": {},
        "content_keywords": {"rag": 4},
        "_post_snapshot": {
            "content_keywords": {"rag": 4},
            "hashtags": {"fastapi": 2},
        },
    }

    with patch(
        "apps.recommendation.services.post_keyword_service.build_post_recommendation_payload",
        AsyncMock(return_value=payload),
    ), patch(
        "apps.recommendation.services.post_keyword_service.persist_post_keyword_snapshot",
        AsyncMock(),
    ), patch(
        "apps.recommendation.services.post_keyword_service.persist_profile_extracted_keywords",
        AsyncMock(
            return_value={
                "latest_post_id": str(post_id),
                "post_ids": [str(post_id)],
                "hashtags": {"fastapi": 2},
                "content_keywords": {"rag": 4},
            }
        ),
    ):
        await log_post_keywords_best_effort(
            post_id,
            {"caption": "Hello #FastAPI"},
            user_id=user_id,
            db=mock_db(),
        )

    assert any(str(post_id) in line for line in printed)
    assert any("fastapi" in line for line in printed)


@pytest.mark.asyncio
async def test_log_post_keywords_best_effort_prints_profile_only_when_content_empty(
    monkeypatch,
    mock_db,
) -> None:
    printed: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: printed.append(" ".join(str(a) for a in args)))

    post_id = uuid4()
    profile = SimpleNamespace(
        major="AI",
        minor=None,
        profile_interests_id=[],
        extracted_keywords=None,
        keywords_updated_at=None,
    )
    post = SimpleNamespace(
        id=post_id,
        extracted_keywords=None,
        keywords_updated_at=None,
    )
    db = mock_db()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: post),
            SimpleNamespace(scalar_one_or_none=lambda: profile),
            SimpleNamespace(scalar_one_or_none=lambda: post),
            SimpleNamespace(scalar_one_or_none=lambda: profile),
        ]
    )
    db.add = MagicMock()
    db.commit = AsyncMock()

    await log_post_keywords_best_effort(post_id, {}, user_id=uuid4(), db=db)

    combined = "\n".join(printed)
    assert '"major": [' in combined
    assert '"minor": []' in combined
    assert '"interests": []' in combined
    assert '"hashtags": {}' in combined
    assert '"engagement_keywords": {}' in combined
    assert '"content_keywords": {}' in combined


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
