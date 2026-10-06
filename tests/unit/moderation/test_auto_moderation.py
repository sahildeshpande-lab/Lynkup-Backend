from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.feed.db_models import Post
from apps.moderation.config import AutoModerationSettings
from apps.moderation.services.auto_moderation_cron import (
    cron_auto_moderation,
    process_auto_moderation,
)
from apps.moderation.services.auto_moderation_service import (
    _clear_moderation_comment_lease_if_owner,
    _clear_moderation_post_lease_if_owner,
    _ensure_moderator_assigned,
    _needs_scan,
    _process_comment,
    _process_post,
    _scan_comment_ids,
    _scan_post_ids,
    extract_post_scan_text,
    find_matching_words,
    scan_posts,
)
from common.enums import PostState
from common.exceptions import ApiError
from core.jobs.claims import ClaimResult


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "blacklist", "expected"),
    [
        ("spam", ["spam"], ["spam"]),
        ("SPAM", ["spam"], ["spam"]),
        ("Spam", ["spam"], ["spam"]),
        ("SpAm", ["spam"], ["spam"]),
        ("This is spam", ["spam"], ["spam"]),
        ("This is SPAM!", ["spam"], ["spam"]),
        ("spam.", ["spam"], ["spam"]),
        ("(spam)", ["spam"], ["spam"]),
        ("spam, please", ["spam"], ["spam"]),
        ("I found spam today", ["spam"], ["spam"]),
        ("spammer", ["spam"], []),
        ("spamming", ["spam"], []),
        ("spablimg", ["spam"], []),
        ("spams", ["spam"], []),
        ("antispam", ["spam"], []),
        ("spamexample", ["spam"], []),
        ("badman", ["bad"], []),
        ("This is spam and this is a scam", ["spam", "scam"], ["spam", "scam"]),
        ("SpAm and SCAM", ["spam"], ["spam"]),
        ("spam! spam? (spam) [spam] spam.", ["spam"], ["spam"]),
        ("spam    spam", ["spam"], ["spam"]),
        ("This is\nspam\ncontent", ["spam"], ["spam"]),
        ("spammer spamming spams antispam spablimg", ["spam"], []),
        ("spam spam SPAM Spam", ["spam"], ["spam"]),
        ("this is spam", [" Spam ", "SPAM"], ["spam"]),
        ("this is spam", [], []),
        ("this is spam", ["", "   "], []),
        ("", ["spam"], []),
        ("clean content", ["spam", "scam"], []),
        ("use c++ today", ["c++"], ["c++"]),
        ("nodejs", ["node.js"], []),
        ("node.js rocks", ["node.js"], ["node.js"]),
        ("a+b", ["a+b"], ["a+b"]),
        ("test?", ["test?"], ["test?"]),
        ("foo.bar", ["foo.bar"], ["foo.bar"]),
        ("foobar", ["foo.bar"], []),
        ("это spam.", ["spam"], ["spam"]),
        ("spamé", ["spam"], []),
    ],
)
def test_find_matching_words_cases(content, blacklist, expected) -> None:
    assert find_matching_words(content, blacklist) == expected


def test_extract_post_scan_text_includes_caption_and_html() -> None:
    post = Post(
        author_user_id=uuid.uuid4(),
        content={
            "caption": "Hello spam",
            "content_html": "<p>More <b>text</b></p>",
            "visibility": "public",
        },
    )
    text = extract_post_scan_text(post)
    assert "Hello spam" in text
    assert "More" in text
    assert "<" not in text


def test_needs_scan_null_and_updated() -> None:
    now = utc_now()
    assert _needs_scan(None, now) is True
    assert _needs_scan(now, now - timedelta(seconds=1)) is False
    assert _needs_scan(now - timedelta(seconds=1), now) is True
    assert _needs_scan(now, now) is False


# ---------------------------------------------------------------------------
# Settings / cron
# ---------------------------------------------------------------------------


def test_auto_moderation_settings_defaults(monkeypatch) -> None:
    monkeypatch.delenv("AUTO_MODERATION_ENABLED", raising=False)
    monkeypatch.delenv("AUTO_MODERATION_CRON_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("AUTO_MODERATION_BATCH_SIZE", raising=False)
    settings = AutoModerationSettings(_env_file=None)
    assert settings.enabled is True
    assert settings.cron_interval_seconds == 120
    assert settings.batch_size == 50


def test_auto_moderation_settings_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AUTO_MODERATION_ENABLED", "false")
    monkeypatch.setenv("AUTO_MODERATION_CRON_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("AUTO_MODERATION_BATCH_SIZE", "10")
    settings = AutoModerationSettings(_env_file=None)
    assert settings.enabled is False
    assert settings.cron_interval_seconds == 30
    assert settings.batch_size == 10


@pytest.mark.asyncio
async def test_cron_respects_disabled_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_cron.auto_moderation_settings",
        MagicMock(enabled=False, cron_interval_seconds=120, batch_size=50),
    )
    scan_mock = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_cron.run_auto_moderation_scan",
        scan_mock,
    )
    await cron_auto_moderation()
    scan_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_cron_uses_configurable_interval_and_batch(monkeypatch) -> None:
    calls: list[int] = []

    async def _scan(*, batch_size=None):
        calls.append(batch_size)

    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_cron.auto_moderation_settings",
        MagicMock(enabled=True, cron_interval_seconds=7, batch_size=11),
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_cron.run_auto_moderation_scan",
        _scan,
    )
    await cron_auto_moderation()
    assert calls == [11]


# ---------------------------------------------------------------------------
# Post processing (mocked session)
# ---------------------------------------------------------------------------


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _post(
    *,
    caption: str = "hello",
    state: PostState = PostState.published,
    moderator_id=None,
    scanned_at=None,
    updated_at=None,
) -> SimpleNamespace:
    now = updated_at or utc_now()
    return SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=uuid.uuid4(),
        state=state,
        content={"caption": caption, "visibility": "public", "content_html": ""},
        caption=caption,
        content_html="",
        moderator_id=moderator_id,
        auto_moderation_scanned_at=scanned_at,
        moderation_words_found=None,
        moderation_notes=None,
        updated_at=now,
        created_at=now,
    )


@pytest.mark.asyncio
async def test_matched_post_becomes_flagged_and_records_history(monkeypatch) -> None:
    mod_id = uuid.uuid4()
    post = _post(caption="This is spam and also a scam", moderator_id=None)
    db = _mock_db()

    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.assign_next_moderator_round_robin",
        AsyncMock(return_value=mod_id),
    )
    history = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.record_moderation_history",
        history,
    )
    monkeypatch.setattr(
        "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
        AsyncMock(),
    )

    await _process_post(db, post, ["spam", "scam"])

    assert post.state == PostState.flagged
    assert post.moderation_words_found == ["spam", "scam"]
    assert post.auto_moderation_scanned_at is not None
    assert post.moderator_id == mod_id
    assert "spam" in (post.moderation_notes or "")
    history.assert_awaited_once()
    assert history.await_args.kwargs["action"] == "flagged"
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_clean_post_keeps_state_and_assigns_moderator(monkeypatch) -> None:
    mod_id = uuid.uuid4()
    post = _post(caption="This is a normal post", moderator_id=None)
    db = _mock_db()

    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.assign_next_moderator_round_robin",
        AsyncMock(return_value=mod_id),
    )
    history = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.record_moderation_history",
        history,
    )

    await _process_post(db, post, ["spam"])

    assert post.state == PostState.published
    assert post.moderation_words_found == []
    assert post.auto_moderation_scanned_at is not None
    assert post.moderator_id == mod_id
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_clean_post_does_not_overwrite_existing_moderator(monkeypatch) -> None:
    existing = uuid.uuid4()
    post = _post(caption="clean", moderator_id=existing)
    db = _mock_db()
    rr = AsyncMock(return_value=uuid.uuid4())
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.assign_next_moderator_round_robin",
        rr,
    )

    await _process_post(db, post, ["spam"])

    assert post.moderator_id == existing
    rr.assert_not_awaited()


@pytest.mark.asyncio
async def test_flagged_post_cleaned_is_not_reinstated(monkeypatch) -> None:
    post = _post(
        caption="This is a normal post",
        state=PostState.flagged,
        moderator_id=uuid.uuid4(),
        scanned_at=utc_now() - timedelta(hours=1),
        updated_at=utc_now(),
    )
    db = _mock_db()
    history = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.record_moderation_history",
        history,
    )

    await _process_post(db, post, ["spam"])

    assert post.state == PostState.flagged
    assert post.moderation_words_found == []
    assert post.auto_moderation_scanned_at is not None
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_edited_post_with_new_blacklist_word_flagged(monkeypatch) -> None:
    scanned = utc_now() - timedelta(hours=1)
    post = _post(
        caption="This is spam",
        moderator_id=uuid.uuid4(),
        scanned_at=scanned,
        updated_at=utc_now(),
    )
    db = _mock_db()
    history = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.record_moderation_history",
        history,
    )
    monkeypatch.setattr(
        "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
        AsyncMock(),
    )

    await _process_post(db, post, ["spam"])

    assert post.state == PostState.flagged
    assert post.moderation_words_found == ["spam"]
    assert post.auto_moderation_scanned_at > scanned
    history.assert_awaited_once()


@pytest.mark.asyncio
async def test_already_scanned_unchanged_post_skipped(monkeypatch) -> None:
    scanned = utc_now()
    post = _post(
        caption="spam",
        scanned_at=scanned,
        updated_at=scanned - timedelta(minutes=1),
    )
    db = _mock_db()
    rr = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.assign_next_moderator_round_robin",
        rr,
    )

    await _process_post(db, post, ["spam"])

    assert post.state == PostState.published
    assert post.auto_moderation_scanned_at == scanned
    rr.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_and_deleted_posts_skipped() -> None:
    db = _mock_db()
    for state in (PostState.draft, PostState.deleted, PostState.rejected):
        post = _post(caption="spam", state=state)
        await _process_post(db, post, ["spam"])
        assert post.state == state
        assert post.auto_moderation_scanned_at is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_assignment_failure_does_not_commit(monkeypatch) -> None:
    post = _post(caption="spam", moderator_id=None)
    db = _mock_db()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.assign_next_moderator_round_robin",
        AsyncMock(side_effect=ApiError("No active moderators")),
    )

    with pytest.raises(ApiError):
        await _process_post(db, post, ["spam"])

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_moderator_uses_existing_rr(monkeypatch) -> None:
    post = _post(moderator_id=None)
    db = _mock_db()
    mod_id = uuid.uuid4()
    rr = AsyncMock(return_value=mod_id)
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.assign_next_moderator_round_robin",
        rr,
    )
    await _ensure_moderator_assigned(post, db)
    assert post.moderator_id == mod_id
    rr.assert_awaited_once_with(db)


# ---------------------------------------------------------------------------
# Comment processing
# ---------------------------------------------------------------------------


def _comment(*, text: str, parent_id=None, scanned_at=None) -> SimpleNamespace:
    now = utc_now()
    return SimpleNamespace(
        id=uuid.uuid4(),
        post_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        parent_comment_id=parent_id,
        comment_text=text,
        is_deleted=False,
        auto_moderation_scanned_at=scanned_at,
        moderation_words_found=None,
        updated_at=now,
        created_at=now,
    )


@pytest.mark.asyncio
async def test_comment_match_soft_deletes(monkeypatch) -> None:
    comment = _comment(text="this is spam")
    db = _mock_db()
    update_count = AsyncMock()
    mark_deleted = AsyncMock(side_effect=lambda db, c, now=None: setattr(c, "is_deleted", True) or c)
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.update_post_comment_count",
        update_count,
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.mark_comment_deleted",
        mark_deleted,
    )

    await _process_comment(db, comment, ["spam"])

    assert comment.is_deleted is True
    assert comment.moderation_words_found == ["spam"]
    assert comment.auto_moderation_scanned_at is not None
    update_count.assert_awaited_once()
    mark_deleted.assert_awaited_once()
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_clean_comment_remains_undeleted(monkeypatch) -> None:
    comment = _comment(text="nice post")
    db = _mock_db()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.update_post_comment_count",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.mark_comment_deleted",
        AsyncMock(),
    )

    await _process_comment(db, comment, ["spam"])

    assert comment.is_deleted is False
    assert comment.moderation_words_found == []
    assert comment.auto_moderation_scanned_at is not None


@pytest.mark.asyncio
async def test_reply_match_does_not_decrement_post_count(monkeypatch) -> None:
    comment = _comment(text="spam reply", parent_id=uuid.uuid4())
    db = _mock_db()
    update_count = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.update_post_comment_count",
        update_count,
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.mark_comment_deleted",
        AsyncMock(side_effect=lambda db, c, now=None: setattr(c, "is_deleted", True) or c),
    )

    await _process_comment(db, comment, ["spam"])

    update_count.assert_not_awaited()
    assert comment.is_deleted is True


# ---------------------------------------------------------------------------
# Batch size wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_posts_uses_batch_size(monkeypatch) -> None:
    fetched = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._fetch_posts_needing_scan",
        fetched,
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.auto_moderation_settings",
        MagicMock(batch_size=50),
    )
    db = _mock_db()
    await scan_posts(db, batch_size=12)
    fetched.assert_awaited_once()
    assert fetched.await_args.kwargs["limit"] == 12


@pytest.mark.asyncio
async def test_scan_posts_defaults_to_settings_batch_size(monkeypatch) -> None:
    fetched = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._fetch_posts_needing_scan",
        fetched,
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.auto_moderation_settings",
        MagicMock(batch_size=50),
    )
    await scan_posts(_mock_db())
    assert fetched.await_args.kwargs["limit"] == 50


@pytest.mark.asyncio
async def test_repeated_non_blacklist_words_do_not_flag(monkeypatch) -> None:
    post = _post(caption="hello hello hello hello hello", moderator_id=uuid.uuid4())
    db = _mock_db()
    history = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.record_moderation_history",
        history,
    )

    await _process_post(db, post, [])

    assert post.state == PostState.published
    assert post.moderation_words_found == []
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_words_on_comment_are_not_deleted(monkeypatch) -> None:
    comment = _comment(text="hello hello hello hello hello")
    db = _mock_db()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.update_post_comment_count",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.mark_comment_deleted",
        AsyncMock(),
    )

    await _process_comment(db, comment, [])

    assert comment.is_deleted is False
    assert comment.moderation_words_found == []


class _SessionCM:
    def __init__(self, session, tracker, name):
        self.session = session
        self.tracker = tracker
        self.name = name

    async def __aenter__(self):
        self.tracker.append(self.name)
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        self.tracker.remove(self.name)
        return False


def _factory_from_sessions(sessions, tracker):
    index = {"n": 0}

    def factory():
        name, session = sessions[index["n"]]
        index["n"] += 1
        return _SessionCM(session, tracker, name)

    return factory


@pytest.mark.asyncio
async def test_scan_posts_claims_before_processing_and_closes_claim_session(
    monkeypatch,
) -> None:
    post_id = uuid.uuid4()
    post = SimpleNamespace(id=post_id)
    tracker: list[str] = []
    process_called = {"n": 0}

    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._fetch_posts_needing_scan",
        AsyncMock(return_value=[post]),
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.claim_moderation_post",
        AsyncMock(return_value=ClaimResult(claimed=True, entity_id=post_id)),
    )

    async def fake_process(session, fresh, words):
        assert "claim" not in tracker
        process_called["n"] += 1

    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._process_post",
        fake_process,
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._load_blacklist",
        AsyncMock(return_value=["spam"]),
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._clear_moderation_post_lease_if_owner",
        AsyncMock(return_value=True),
    )

    work_db = _mock_db()
    work_db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: post))
    complete_db = _mock_db()
    claim_db = _mock_db()
    factory = _factory_from_sessions(
        [("claim", claim_db), ("work", work_db), ("complete", complete_db)],
        tracker,
    )

    processed = await scan_posts(
        _mock_db(),
        batch_size=1,
        session_factory=factory,
        lease_owner="worker-1",
    )

    assert processed == 1
    assert process_called["n"] == 1
    assert tracker == []


@pytest.mark.asyncio
async def test_failed_claim_skips_processing(monkeypatch) -> None:
    post_id = uuid.uuid4()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.claim_moderation_post",
        AsyncMock(return_value=ClaimResult(claimed=False, entity_id=post_id)),
    )
    process = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._process_post",
        process,
    )
    claim_db = _mock_db()
    tracker: list[str] = []
    factory = _factory_from_sessions([("claim", claim_db)], tracker)

    processed = await _scan_post_ids(
        [post_id],
        session_factory=factory,
        lease_owner="worker-1",
    )

    assert processed == 0
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_processing_does_not_clear_lease(monkeypatch) -> None:
    post_id = uuid.uuid4()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.claim_moderation_post",
        AsyncMock(return_value=ClaimResult(claimed=True, entity_id=post_id)),
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._load_blacklist",
        AsyncMock(return_value=["spam"]),
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._process_post",
        AsyncMock(side_effect=RuntimeError("per-item failure")),
    )
    clear = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._clear_moderation_post_lease_if_owner",
        clear,
    )

    work_db = _mock_db()
    work_db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=post_id))
    )
    claim_db = _mock_db()
    tracker: list[str] = []
    factory = _factory_from_sessions(
        [("claim", claim_db), ("work", work_db)],
        tracker,
    )

    processed = await _scan_post_ids(
        [post_id],
        session_factory=factory,
        lease_owner="worker-1",
    )

    assert processed == 0
    clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_processing_clears_lease_only_for_owner(mock_db) -> None:
    db = mock_db(SimpleNamespace(rowcount=1))
    post_id = uuid.uuid4()

    cleared = await _clear_moderation_post_lease_if_owner(
        db,
        post_id=post_id,
        lease_owner="worker-1",
    )

    assert cleared is True
    stmt = db.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "moderation_lease_owner" in compiled
    assert "worker-1" in compiled
    assert "auto_moderation_scanned_at" not in compiled.split("set", 1)[-1]


@pytest.mark.asyncio
async def test_stale_worker_cannot_clear_newer_post_lease(mock_db) -> None:
    db = mock_db(SimpleNamespace(rowcount=0))
    post_id = uuid.uuid4()

    cleared = await _clear_moderation_post_lease_if_owner(
        db,
        post_id=post_id,
        lease_owner="stale-worker",
    )

    assert cleared is False
    stmt = db.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "lease_owner" in compiled
    assert "stale-worker" in compiled


@pytest.mark.asyncio
async def test_stale_worker_cannot_clear_newer_comment_lease(mock_db) -> None:
    db = mock_db(SimpleNamespace(rowcount=0))
    comment_id = uuid.uuid4()

    cleared = await _clear_moderation_comment_lease_if_owner(
        db,
        comment_id=comment_id,
        lease_owner="stale-worker",
    )

    assert cleared is False


@pytest.mark.asyncio
async def test_comment_scan_claims_before_processing(monkeypatch) -> None:
    comment_id = uuid.uuid4()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service.claim_moderation_comment",
        AsyncMock(return_value=ClaimResult(claimed=True, entity_id=comment_id)),
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._load_blacklist",
        AsyncMock(return_value=[]),
    )
    process = AsyncMock()
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._process_comment",
        process,
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_service._clear_moderation_comment_lease_if_owner",
        AsyncMock(return_value=True),
    )
    work_db = _mock_db()
    work_db.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id=comment_id))
    )
    tracker: list[str] = []
    factory = _factory_from_sessions(
        [("claim", _mock_db()), ("work", work_db), ("complete", _mock_db())],
        tracker,
    )

    processed = await _scan_comment_ids(
        [comment_id],
        session_factory=factory,
        lease_owner="worker-1",
    )

    assert processed == 1
    process.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_cron_wrapper_still_swallows_outer_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_cron.auto_moderation_settings",
        MagicMock(enabled=True, cron_interval_seconds=120, batch_size=50),
    )
    monkeypatch.setattr(
        "apps.moderation.services.auto_moderation_cron.run_auto_moderation_scan",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    await process_auto_moderation()
