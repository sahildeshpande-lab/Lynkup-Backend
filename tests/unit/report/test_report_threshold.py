from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.report.schemas import ReportCreateRequest
from apps.report.services import report_service as svc
from common.enums import PostState, ReportEntityType, ReportStatus, UserStatus


def _user(**overrides):
    data = {
        "id": uuid.uuid4(),
        "email": "user@example.com",
        "firebase_uid": "fb-uid",
        "is_deleted": False,
        "deleted_at": None,
        "status": UserStatus.active,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _post(**overrides):
    data = {
        "id": uuid.uuid4(),
        "author_user_id": uuid.uuid4(),
        "state": PostState.published,
        "moderator_id": uuid.uuid4(),
        "is_moderator_reviewed": False,
        "reviewed_at": None,
        "updated_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _comment(**overrides):
    data = {
        "id": uuid.uuid4(),
        "post_id": uuid.uuid4(),
        "parent_comment_id": None,
        "is_deleted": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _report(**overrides):
    data = {
        "id": uuid.uuid4(),
        "reported_id": uuid.uuid4(),
        "entity_type": ReportEntityType.post,
        "entity_id": uuid.uuid4(),
        "reason": "Spam",
        "status": ReportStatus.under_review,
        "moderator_id": None,
        "admin_comment": None,
        "post_revision_id": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_post_threshold_flags_once_and_keeps_moderator(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    post = _post(moderator_id=moderator_id, state=PostState.published)
    revision = SimpleNamespace(id=uuid.uuid4())
    report = _report(moderator_id=moderator_id, entity_id=post.id)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam",
    )
    db = mock_db(scalar_result(post), scalar_result(None))

    with (
        patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision)),
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=moderator_id)),
        patch.object(svc, "create_report", AsyncMock(return_value=report)),
        patch.object(svc, "get_enabled_moderation_threshold", AsyncMock(return_value=3)),
        patch.object(svc, "count_reports_for_entity", AsyncMock(return_value=3)),
        patch(
            "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
            AsyncMock(),
        ),
        patch.object(svc, "_batch_moderator_names", AsyncMock(return_value={moderator_id: "Mod"})),
        patch(
            "apps.notifications.services.notify_post_author",
            AsyncMock(),
        ) as notify,
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert post.state == PostState.flagged
    assert post.moderator_id == moderator_id
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["notification_type"] == "POST_FLAGGED"


@pytest.mark.asyncio
async def test_post_threshold_below_does_not_flag(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    post = _post(moderator_id=moderator_id, state=PostState.published)
    revision = SimpleNamespace(id=uuid.uuid4())
    report = _report(moderator_id=moderator_id, entity_id=post.id)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam",
    )
    db = mock_db(scalar_result(post), scalar_result(None))

    with (
        patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision)),
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=moderator_id)),
        patch.object(svc, "create_report", AsyncMock(return_value=report)),
        patch.object(svc, "get_enabled_moderation_threshold", AsyncMock(return_value=3)),
        patch.object(svc, "count_reports_for_entity", AsyncMock(return_value=2)),
        patch.object(svc, "_batch_moderator_names", AsyncMock(return_value={})),
        patch("apps.notifications.services.notify_post_author", AsyncMock()) as notify,
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert post.state == PostState.published
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_already_flagged_skips_duplicate_action(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    post = _post(moderator_id=moderator_id, state=PostState.flagged)
    revision = SimpleNamespace(id=uuid.uuid4())
    report = _report(moderator_id=moderator_id, entity_id=post.id)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam",
    )
    db = mock_db(scalar_result(post), scalar_result(None))

    with (
        patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision)),
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=moderator_id)),
        patch.object(svc, "create_report", AsyncMock(return_value=report)),
        patch.object(svc, "get_enabled_moderation_threshold", AsyncMock(return_value=3)),
        patch.object(svc, "count_reports_for_entity", AsyncMock(return_value=5)),
        patch.object(svc, "_batch_moderator_names", AsyncMock(return_value={})),
        patch("apps.notifications.services.notify_post_author", AsyncMock()) as notify,
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert post.state == PostState.flagged
    assert post.moderator_id == moderator_id
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_duplicate_same_revision_rejected(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    post = _post()
    revision = SimpleNamespace(id=uuid.uuid4())
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam",
    )
    db = mock_db(scalar_result(post), scalar_result(_report()))

    with patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision)):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is False
    assert "already reported" in response.message.lower()


@pytest.mark.asyncio
async def test_comment_threshold_soft_deletes(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    comment = _comment(is_deleted=False)
    report = _report(entity_type=ReportEntityType.comment, entity_id=comment.id)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.comment,
        entity_id=comment.id,
        reason="Abuse",
    )
    db = mock_db(scalar_result(comment), scalar_result(None), scalar_result(None))

    with (
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=None)),
        patch.object(svc, "create_report", AsyncMock(return_value=report)),
        patch.object(svc, "get_enabled_moderation_threshold", AsyncMock(return_value=2)),
        patch.object(svc, "count_reports_for_entity", AsyncMock(return_value=2)),
        patch.object(svc, "update_post_comment_count", AsyncMock()),
        patch.object(svc, "mark_comment_deleted", AsyncMock()) as mark_deleted,
        patch.object(svc, "_batch_moderator_names", AsyncMock(return_value={})),
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    mark_deleted.assert_awaited_once()


@pytest.mark.asyncio
async def test_comment_already_deleted_skips_threshold_action(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    comment = _comment(is_deleted=True)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.comment,
        entity_id=comment.id,
        reason="Abuse",
    )
    db = mock_db(scalar_result(comment))

    response = await svc.create_report_service(db, reporter_id, payload)
    assert response.status is False
    assert "soft-deleted comment" in response.message.lower()


@pytest.mark.asyncio
async def test_user_threshold_suspends_once(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    target = _user(status=UserStatus.active)
    report = _report(entity_type=ReportEntityType.user, entity_id=target.id)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.user,
        entity_id=target.id,
        reason="Harassment",
    )
    db = mock_db(scalar_result(target), scalar_result(None))

    with (
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=None)),
        patch.object(svc, "create_report", AsyncMock(return_value=report)),
        patch.object(svc, "get_enabled_moderation_threshold", AsyncMock(return_value=2)),
        patch.object(svc, "count_reports_for_entity", AsyncMock(return_value=2)),
        patch(
            "apps.moderation.services.record_moderation_history",
            AsyncMock(),
        ),
        patch.object(svc, "_batch_moderator_names", AsyncMock(return_value={})),
        patch("apps.notifications.services.notify_account_status", AsyncMock()) as notify,
        patch("core.auth.services.disable_firebase_user") as disable_fb,
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert target.status == UserStatus.suspended
    notify.assert_awaited_once()
    disable_fb.assert_called_once_with("fb-uid")


@pytest.mark.asyncio
async def test_user_already_suspended_skips_action(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    target = _user(status=UserStatus.suspended)
    report = _report(entity_type=ReportEntityType.user, entity_id=target.id)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.user,
        entity_id=target.id,
        reason="Harassment",
    )
    db = mock_db(scalar_result(target), scalar_result(None))

    with (
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=None)),
        patch.object(svc, "create_report", AsyncMock(return_value=report)),
        patch.object(svc, "get_enabled_moderation_threshold", AsyncMock(return_value=2)),
        patch.object(svc, "count_reports_for_entity", AsyncMock(return_value=5)),
        patch.object(svc, "_batch_moderator_names", AsyncMock(return_value={})),
        patch("apps.notifications.services.notify_account_status", AsyncMock()) as notify,
        patch("core.auth.services.disable_firebase_user") as disable_fb,
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert target.status == UserStatus.suspended
    notify.assert_not_awaited()
    disable_fb.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_threshold_skips_action(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    post = _post(moderator_id=moderator_id, state=PostState.published)
    revision = SimpleNamespace(id=uuid.uuid4())
    report = _report(moderator_id=moderator_id, entity_id=post.id)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam",
    )
    db = mock_db(scalar_result(post), scalar_result(None))

    with (
        patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision)),
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=moderator_id)),
        patch.object(svc, "create_report", AsyncMock(return_value=report)),
        patch.object(svc, "get_enabled_moderation_threshold", AsyncMock(return_value=None)),
        patch.object(svc, "count_reports_for_entity", AsyncMock(return_value=100)),
        patch.object(svc, "_batch_moderator_names", AsyncMock(return_value={})),
        patch("apps.notifications.services.notify_post_author", AsyncMock()) as notify,
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert post.state == PostState.published
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_post_threshold_uses_revision_scoped_count(mock_db):
    post = _post(state=PostState.published)
    revision = SimpleNamespace(id=uuid.uuid4())
    db = mock_db()

    with (
        patch.object(svc, "get_enabled_moderation_threshold", AsyncMock(return_value=3)),
        patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision)),
        patch.object(svc, "count_reports_for_entity", AsyncMock(return_value=3)) as count,
        patch(
            "apps.profiles.services.profile_stats_service.decrement_posts_count_for_user",
            AsyncMock(),
        ),
        patch.object(svc, "assign_next_moderator_round_robin", AsyncMock()) as assign_rr,
    ):
        changed = await svc._apply_post_report_threshold(db, post)

    assert changed is True
    assert post.state == PostState.flagged
    count.assert_awaited_once_with(
        db,
        ReportEntityType.post,
        post.id,
        post_revision_id=revision.id,
    )
    assign_rr.assert_not_awaited()


@pytest.mark.asyncio
async def test_integrity_error_returns_duplicate_message(mock_db, scalar_result):
    from sqlalchemy.exc import IntegrityError

    reporter_id = uuid.uuid4()
    post = _post()
    revision = SimpleNamespace(id=uuid.uuid4())
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam",
    )
    db = mock_db(scalar_result(post), scalar_result(None))

    with (
        patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision)),
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=None)),
        patch.object(
            svc,
            "create_report",
            AsyncMock(side_effect=IntegrityError("stmt", {}, Exception("dup"))),
        ),
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is False
    assert "already reported" in response.message.lower()
    db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_new_revision_allows_same_reporter(mock_db, scalar_result):
    """Same reporter may report again after the post gets a new revision."""
    reporter_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    post = _post(moderator_id=moderator_id)
    revision_b = SimpleNamespace(id=uuid.uuid4())
    report = _report(moderator_id=moderator_id, entity_id=post.id, post_revision_id=revision_b.id)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam",
    )
    # Duplicate check returns None for revision B (app-level).
    db = mock_db(scalar_result(post), scalar_result(None))

    with (
        patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision_b)),
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=moderator_id)),
        patch.object(svc, "create_report", AsyncMock(return_value=report)) as create_report,
        patch.object(svc, "_apply_post_report_threshold", AsyncMock(return_value=False)),
        patch.object(svc, "_batch_moderator_names", AsyncMock(return_value={})),
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert create_report.await_args.kwargs["post_revision_id"] == revision_b.id


@pytest.mark.asyncio
async def test_threshold_auto_resolves_reports_to_actioned(mock_db):
    from apps.report.db_models.report_db_model import Report
    from common.enums import ReportStatus

    post = _post()
    revision = SimpleNamespace(id=uuid.uuid4())

    report_1 = _report(status=ReportStatus.under_review, entity_id=post.id)
    report_2 = _report(status=ReportStatus.under_review, entity_id=post.id)

    db = mock_db()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [report_1, report_2]
    db.execute = AsyncMock(return_value=execute_result)

    with (
        patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision)),
        patch.object(svc, "get_enabled_moderation_threshold", AsyncMock(return_value=3)),
        patch.object(svc, "count_reports_for_entity", AsyncMock(return_value=3)),
        patch("apps.profiles.services.profile_stats_service.decrement_posts_count_for_user", AsyncMock()),
    ):
        result = await svc._apply_post_report_threshold(db, post)

    assert result is True
    assert report_1.status == ReportStatus.actioned
    assert report_1.admin_comment == "Actioned performed by System"
    assert report_2.status == ReportStatus.actioned
    assert report_2.admin_comment == "Actioned performed by System"

