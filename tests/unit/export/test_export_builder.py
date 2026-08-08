from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, Mock

import pytest

from apps.accounts.db_models import User
from apps.export.builder import DataExportBuilder, dumps_json
from apps.profiles.db_models import Profile
from common.enums import ProfileVisibility, UserStatus, OnboardingStatus
from tests.unit.conftest import FakeScalarResult


SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "token",
    "tokens",
    "fcm_token",
    "device_id",
    "apns",
    "firebase_uid",
    "refresh_token",
    "moderator_id",
    "moderation_notes",
}


def _contains_sensitive(payload) -> list[str]:
    found: list[str] = []

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_l = str(key).lower()
                if key_l in SENSITIVE_KEYS or any(s in key_l for s in SENSITIVE_KEYS):
                    found.append(f"{path}.{key}" if path else key)
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                walk(item, f"{path}[{idx}]")

    walk(payload)
    return found


@pytest.mark.asyncio
async def test_builder_creates_valid_zip_with_core_files():
    user_id = uuid4()
    export_id = uuid4()
    user = User(
        id=user_id,
        email="jane@example.com",
        status=UserStatus.active,
        onboarding_status=OnboardingStatus.completed,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    profile = Profile(
        user_id=user_id,
        first_name="Jane",
        last_name="Doe",
        bio="Hello",
        major="CS",
        profile_visibility=ProfileVisibility.public,
        updated_at=datetime.now(timezone.utc),
    )

    results = [
        FakeScalarResult(value=user),  # build_profile user
        FakeScalarResult(value=profile),  # build_profile profile
        FakeScalarResult(values=[]),  # posts
        FakeScalarResult(values=[]),  # comments
        FakeScalarResult(values=[]),  # post reactions
        FakeScalarResult(values=[]),  # comment reactions
        FakeScalarResult(values=[]),  # bookmarks
        FakeScalarResult(values=[]),  # connections
        FakeScalarResult(values=[]),  # requests
        FakeScalarResult(values=[]),  # follows
        FakeScalarResult(values=[]),  # blocks
        FakeScalarResult(values=[]),  # notifications
        FakeScalarResult(value=profile),  # learning profile
        FakeScalarResult(values=[]),  # learning logs
    ]

    db = Mock()
    db.execute = AsyncMock(side_effect=results)

    builder = DataExportBuilder(db=db, user_id=user_id, export_id=export_id)
    zip_bytes = await builder.build_zip_bytes()

    assert zipfile.is_zipfile(io.BytesIO(zip_bytes))
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
        assert "README.txt" in names
        assert "profile.json" in names
        assert "posts.json" in names
        assert "comments.json" in names
        assert "reactions.json" in names
        assert "bookmarks.json" in names
        assert "connections.json" in names
        assert "notifications.json" in names
        assert "learning/learning_profile.json" in names
        assert "learning/recommendations.json" in names

        profile_data = json.loads(zf.read("profile.json"))
        assert profile_data["email"] == "jane@example.com"
        assert profile_data["first_name"] == "Jane"
        sensitive = _contains_sensitive(profile_data)
        assert sensitive == [], sensitive

        assert "password_hash" not in profile_data
        assert "firebase_uid" not in profile_data
        assert json.loads(zf.read("posts.json")) == []
        assert "Export ID:" in zf.read("README.txt").decode("utf-8")


def test_dumps_json_handles_uuid_enum_datetime():
    from common.enums import ReactionType
    from uuid import UUID

    payload = {
        "id": UUID("12345678-1234-5678-1234-567812345678"),
        "type": ReactionType.like,
        "at": datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc),
        "nested": None,
    }
    text = dumps_json(payload)
    loaded = json.loads(text)
    assert loaded["id"] == "12345678-1234-5678-1234-567812345678"
    assert loaded["type"] == "like"
    assert loaded["at"].startswith("2026-08-08")
    assert loaded["nested"] is None
