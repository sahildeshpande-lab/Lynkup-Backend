from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import pytest

from apps.feed.schemas import PostDetailData, RepostedPostData, PostContentData
from apps.connections.schemas import (
    ConnectionListItemResponse,
    RecommendedUserResponse,
    PendingLynkupRequestResponse,
)
from apps.feed.services.profile_enrichment import load_profile_details
from apps.connections.services.connection_service import get_pending_requests
from apps.connections.services.recommendation_service import get_recommendations_categorized


def test_feed_schemas_support_university_details() -> None:
    uni_details = {
        "id": "e4d3a2b1-5717-4562-b3fc-2c963f66afa6",
        "university_name": "Stanford University",
        "university_website": "https://stanford.edu",
    }
    
    post = PostDetailData(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state="published",
        revision_number=1,
        content=PostContentData(caption="Test caption"),
        created_at="2026-08-25T12:00:00Z",
        updated_at="2026-08-25T12:00:00Z",
        university="Stanford University",
        university_details=uni_details,
    )
    assert post.university == "Stanford University"
    assert post.university_details == uni_details
    assert post.university_details["university_website"] == "https://stanford.edu"

    reposted = RepostedPostData(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state="published",
        revision_number=1,
        content=PostContentData(caption="Original caption"),
        created_at="2026-08-25T12:00:00Z",
        updated_at="2026-08-25T12:00:00Z",
        university="Stanford University",
        university_details=uni_details,
    )
    assert reposted.university_details == uni_details


def test_connection_schemas_support_university_details() -> None:
    uni_details = {
        "id": "e4d3a2b1-5717-4562-b3fc-2c963f66afa6",
        "university_name": "Stanford University",
        "university_website": "https://stanford.edu",
    }
    
    rec = RecommendedUserResponse(
        user_id=uuid.uuid4(),
        score=10.0,
        university="Stanford University",
        university_details=uni_details,
    )
    assert rec.university == "Stanford University"
    assert rec.university_details == uni_details

    pending = PendingLynkupRequestResponse(
        lynkup_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status="pending",
        university="Stanford University",
        university_details=uni_details,
    )
    assert pending.university == "Stanford University"
    assert pending.university_details == uni_details

    connection = ConnectionListItemResponse(
        lynkup_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status="accepted",
        university="Stanford University",
        university_details=uni_details,
        major="Zoology/Animal Biology",
        minor="Animal Genetics",
        edu_level="Masters",
    )
    assert connection.university == "Stanford University"
    assert connection.university_details == uni_details
    assert connection.major == "Zoology/Animal Biology"
    assert connection.minor == "Animal Genetics"
    assert connection.edu_level == "Masters"


@pytest.mark.asyncio
async def test_load_profile_details_builds_university_details(mock_db) -> None:
    user_id = uuid.uuid4()
    uni_id = uuid.uuid4()
    profile = SimpleNamespace(
        user_id=user_id,
        university_id=uni_id,
        profile_interests_id=[],
        bio="Test bio",
        major="Computer Science",
        minor="Math",
        edu_level="Bachelors",
    )
    
    uni_row = SimpleNamespace(id=uni_id, name="Stanford University", website="https://stanford.edu")
    db = mock_db()
    
    mock_execute_result = SimpleNamespace(all=lambda: [uni_row])
    db.execute = AsyncMock(return_value=mock_execute_result)
    
    result = await load_profile_details(db, {user_id: profile})
    assert user_id in result
    assert result[user_id]["university"] == "Stanford University"
    assert result[user_id]["university_details"] == {
        "id": str(uni_id),
        "university_name": "Stanford University",
        "university_website": "https://stanford.edu",
    }

