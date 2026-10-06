from __future__ import annotations

import uuid
from types import SimpleNamespace

from common.enums import ProfileVisibility
from apps.feed.services.post_service import (
    _format_reviewed_post_item,
    _normalize_profile_visibility,
    format_post_detail,
    format_repost_item,
)


def test_normalize_profile_visibility_private_only():
    assert _normalize_profile_visibility(None) == "private"
    assert _normalize_profile_visibility(SimpleNamespace(profile_visibility=None)) == "private"
    assert _normalize_profile_visibility(SimpleNamespace(profile_visibility="public")) == "public"
    assert _normalize_profile_visibility(
        SimpleNamespace(profile_visibility=ProfileVisibility.public)
    ) == "public"
    assert _normalize_profile_visibility(
        SimpleNamespace(profile_visibility=ProfileVisibility.connections_only)
    ) == "private"
    assert _normalize_profile_visibility(
        SimpleNamespace(profile_visibility="connections_only")
    ) == "private"
    assert _normalize_profile_visibility(SimpleNamespace(profile_visibility="private")) == "private"
    assert _normalize_profile_visibility(
        SimpleNamespace(profile_visibility=ProfileVisibility.private)
    ) == "private"


def test_format_post_detail_author_name_falls_back_to_email():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )
    author_user = SimpleNamespace(email="author@example.com")

    data = format_post_detail(post, author_user=author_user)

    assert data["author_name"] == "author@example.com"


def test_format_post_detail_includes_profile_visibility():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )
    author_profile = SimpleNamespace(
        first_name="Ada",
        last_name="Lovelace",
        profile_photo_url=None,
        profile_visibility=ProfileVisibility.private,
    )

    data = format_post_detail(post, author_profile=author_profile)
    assert data["author_name"] == "Ada Lovelace"
    assert data["profile_visibility"] == "private"
    assert data["first_name"] == "Ada"
    assert data["last_name"] == "Lovelace"
    assert data["is_connected"] is False
    assert data["is_requested"] is False


def test_format_post_detail_keeps_author_fields_when_profile_is_private():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="reinstate"),
        revision_number=14,
        content={"caption": "", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-02T00:00:00Z",
        is_edited=True,
        like_count=5,
        repost_count=2,
        share_count=2,
        comment_count=1,
        is_moderator_reviewed=True,
        reviewed_at="2026-01-02T00:00:00Z",
        moderator_id=uuid.uuid4(),
        moderation_notes="Great edit",
        attachments=[],
    )
    author_profile = SimpleNamespace(
        first_name="Ada",
        last_name="Lovelace",
        profile_photo_url=None,
        profile_visibility=ProfileVisibility.private,
    )
    profile_details = {
        "university": "1 December University of Alba Iulia",
        "university_details": {
            "id": "be7dc57f-2cd4-5540-88cc-ebd3b83a5f91",
            "university_name": "1 December University of Alba Iulia",
            "university_website": "http://www.uab.ro/",
        },
        "bio": "Test",
        "academic_interest": ["Taxation", "Forensic Accounting"],
        "major": "Accounting",
        "minor": "Accounting And Business/Management",
        "education_level": "MD",
        "profile_visibility": "should-not-overwrite",
    }

    data = format_post_detail(
        post,
        author_profile=author_profile,
        profile_details=profile_details,
        is_connected=False,
        is_requested=False,
        is_liked=False,
        is_reposted=False,
        is_bookmarked=False,
        user_reaction=None,
        viewer_user_id=uuid.uuid4(),
    )

    assert data["profile_visibility"] == "private"
    assert data["first_name"] == "Ada"
    assert data["last_name"] == "Lovelace"
    assert data["university"] == "1 December University of Alba Iulia"
    assert data["university_details"]["university_name"] == "1 December University of Alba Iulia"
    assert data["bio"] == "Test"
    assert data["academic_interest"] == ["Taxation", "Forensic Accounting"]
    assert data["major"] == "Accounting"
    assert data["minor"] == "Accounting And Business/Management"
    assert data["education_level"] == "MD"
    assert data["is_connected"] is False
    assert data["is_requested"] is False
    assert data["status"] == "reinstate"
    assert data["moderation_notes"] == "Great edit"


def test_format_post_detail_includes_engagement_fields():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=15,
        repost_count=4,
        share_count=10,
        comment_count=2,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )

    data = format_post_detail(
        post,
        is_liked=True,
        is_reposted=False,
        is_bookmarked=True,
        user_reaction="LIKE",
        viewer_user_id=uuid.uuid4(),
    )

    assert data["like_count"] == 15
    assert data["repost_count"] == 4
    assert data["share_count"] == 10
    assert data["comment_count"] == 2
    assert data["is_liked"] is True
    assert data["is_reposted"] is False
    assert data["is_bookmarked"] is True
    assert data["is_repostable"] is True
    assert data["user_reaction"] == "LIKE"
    assert data["reposted_data"] is None
    assert data["profile_visibility"] == "private"
    assert data["is_edited"] is False
    assert data["triggered_moderation_review"] is False
    assert data["updated_at"] == "2026-01-01T00:00:00Z"
    assert data["is_connected"] is False
    assert data["is_requested"] is False
    assert data["university"] is None
    assert data["academic_interest"] == []
    assert data["status"] == "published"


def test_format_post_detail_includes_is_edited():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="published"),
        revision_number=3,
        content={"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-02T00:00:00Z",
        is_edited=True,
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )

    data = format_post_detail(post, triggered_moderation_review=True)
    assert data["is_edited"] is True
    assert data["triggered_moderation_review"] is True
    assert data["updated_at"] == "2026-01-02T00:00:00Z"
    assert data["revision_number"] == 3


def test_format_post_detail_is_repostable_false_for_author():
    author_id = uuid.uuid4()
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=author_id,
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "content_html": "<p>hello</p>", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )

    data = format_post_detail(post, viewer_user_id=author_id)
    assert data["is_repostable"] is False


def test_format_reviewed_post_item_includes_engagement_counts():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        content={"caption": "reviewed", "content_html": "<p>ok</p>"},
        state=SimpleNamespace(value="published"),
        is_moderator_reviewed=True,
        reviewed_at="2026-07-14T10:00:00Z",
        created_at="2026-07-14T09:00:00Z",
        updated_at="2026-07-14T11:00:00Z",
        is_edited=True,
        revision_number=2,
        like_count=12,
        repost_count=3,
        share_count=7,
        comment_count=5,
        moderator_id=uuid.uuid4(),
        attachments=[],
    )
    profile = SimpleNamespace(
        first_name="Ada",
        last_name="Lovelace",
        profile_photo_url=None,
    )

    data = _format_reviewed_post_item(post, profile, report_count=4)

    assert data["like_count"] == 12
    assert data["repost_count"] == 3
    assert data["share_count"] == 7
    assert data["comment_count"] == 5
    assert data["report_count"] == 4
    assert data["first_name"] == "Ada"
    assert data["is_edited"] is True
    assert data["triggered_moderation_review"] is False
    assert data["updated_at"] == "2026-07-14T11:00:00Z"
    assert data["revision_number"] == 2


def test_format_reviewed_post_item_includes_triggered_moderation_review():
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        content={"caption": "reopened", "content_html": "<p>ok</p>"},
        state=SimpleNamespace(value="processing"),
        is_moderator_reviewed=False,
        reviewed_at=None,
        created_at="2026-07-14T09:00:00Z",
        updated_at="2026-07-14T11:00:00Z",
        is_edited=True,
        revision_number=3,
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        moderator_id=uuid.uuid4(),
        attachments=[],
    )
    profile = SimpleNamespace(first_name="Ada", last_name="Lovelace", profile_photo_url=None)

    data = _format_reviewed_post_item(
        post, profile, triggered_moderation_review=True
    )

    assert data["is_edited"] is True
    assert data["triggered_moderation_review"] is True
    assert data["status"] == "processing"


def test_format_repost_item_separates_reposter_and_author_visibility():
    """Top-level visibility is the reposter; nested is the original author."""
    from datetime import datetime, timezone

    author_id = uuid.uuid4()
    reposter_id = uuid.uuid4()
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=author_id,
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "orig", "content_html": "<p>orig</p>", "visibility": "public"},
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        is_edited=False,
        like_count=0,
        repost_count=1,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )
    original_author = SimpleNamespace(
        user_id=author_id,
        first_name="Alice",
        last_name="Author",
        profile_photo_url=None,
        profile_visibility=ProfileVisibility.private,
    )
    reposter = SimpleNamespace(
        user_id=reposter_id,
        first_name="Bob",
        last_name="Reposter",
        profile_photo_url=None,
        profile_visibility=ProfileVisibility.public,
    )

    data = format_repost_item(
        post,
        original_author_profile=original_author,
        reposter_profile=reposter,
        repost_id=uuid.uuid4(),
        reposted_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
        original_author_details={"university": "Orig U", "profile_visibility": "should-not-leak"},
        reposter_details={"university": "Repost U", "profile_visibility": "should-not-leak"},
    )

    assert data["profile_visibility"] == "public"
    assert data["university"] == "Repost U"
    nested = data["reposted_data"]
    assert nested is not None
    assert nested["profile_visibility"] == "private"
    assert nested["university"] == "Orig U"
    # No duplicate/ambiguous visibility at the same level
    assert list(data.keys()).count("profile_visibility") == 1
    assert list(nested.keys()).count("profile_visibility") == 1


def test_format_post_detail_includes_original_post_account_status():
    from common.enums import UserStatus

    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )
    author_user_suspended = SimpleNamespace(email="author@example.com", status=UserStatus.suspended)
    data = format_post_detail(post, author_user=author_user_suspended)
    assert data["original_post_account_status"] == "Suspended"

    author_user_banned = SimpleNamespace(email="author@example.com", status=UserStatus.banned)
    data_banned = format_post_detail(post, author_user=author_user_banned)
    assert data_banned["original_post_account_status"] == "Banned"

    author_user_active = SimpleNamespace(email="author@example.com", status=UserStatus.active)
    data_active = format_post_detail(post, author_user=author_user_active)
    assert data_active["original_post_account_status"] is None


def test_format_repost_item_includes_original_post_account_status():
    from datetime import datetime, timezone
    from common.enums import UserStatus

    author_id = uuid.uuid4()
    reposter_id = uuid.uuid4()
    post = SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=author_id,
        state=SimpleNamespace(value="published"),
        revision_number=1,
        content={"caption": "hello", "visibility": "public"},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        like_count=0,
        repost_count=0,
        share_count=0,
        comment_count=0,
        is_moderator_reviewed=False,
        reviewed_at=None,
        moderator_id=None,
        attachments=[],
    )
    original_author_profile = SimpleNamespace(user_id=author_id, first_name="Alice", last_name="A", profile_photo_url=None)
    reposter_profile = SimpleNamespace(user_id=reposter_id, first_name="Bob", last_name="B", profile_photo_url=None)
    original_author_user = SimpleNamespace(status=UserStatus.suspended)

    data = format_repost_item(
        post,
        original_author_profile=original_author_profile,
        reposter_profile=reposter_profile,
        original_author_user=original_author_user,
        repost_id=uuid.uuid4(),
        reposted_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
    )

    assert data["original_post_account_status"] == "Suspended"
    assert data["reposted_data"]["original_post_account_status"] == "Suspended"

