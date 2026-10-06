"""Business-rule tests for report_count vs report history retention."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.administration.schemas import AdminUserStatus
from apps.feed.services import post_service as post_svc
from apps.report.schemas import ReportCreateRequest, ReportReviewRequest
from apps.report.services import report_service as report_svc
from common.enums import PostState, ReportEntityType, ReportStatus, UserStatus


def _post(*, state: PostState = PostState.published, author_user_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=author_user_id or uuid.uuid4(),
        state=state,
        content={"visibility": "public"},
        moderator_id=None,
        is_moderator_reviewed=False,
        reviewed_at=None,
        revision_number=1,
        updated_at=None,
        moderation_notes=None,
    )


def _user(*, status: UserStatus = UserStatus.active):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="user@example.com",
        status=status,
        firebase_uid=None,
        is_deleted=False,
        deleted_at=None,
        roles=[],
        updated_at=None,
    )


def _report(*, entity_id=None, entity_type=ReportEntityType.post, status=ReportStatus.under_review):
    return SimpleNamespace(
        id=uuid.uuid4(),
        reported_id=uuid.uuid4(),
        entity_type=entity_type,
        entity_id=entity_id or uuid.uuid4(),
        reason="spam",
        status=status,
        moderator_id=None,
        admin_comment=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        post_revision_id=None,
        is_deleted=False,
        counts_in_queue=True,
    )


@pytest.mark.asyncio
async def test_create_report_increments_queue_count_via_new_row(mock_db, scalar_result):
    """Test A — POST /reports adds an in-queue report row (+1 derived count)."""
    reporter_id = uuid.uuid4()
    post = _post()
    revision = SimpleNamespace(id=uuid.uuid4())
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Inappropriate",
    )
    db = mock_db(scalar_result(post))

    with (
        patch.object(report_svc, "get_latest_post_revision", AsyncMock(return_value=revision)),
        patch.object(report_svc, "get_duplicate_report", AsyncMock(return_value=None)),
        patch.object(report_svc, "_resolve_report_moderator_id", AsyncMock(return_value=None)),
        patch.object(report_svc, "create_report", AsyncMock(return_value=_report(entity_id=post.id))) as create_report,
        patch.object(report_svc, "_apply_post_report_threshold", AsyncMock(return_value=False)),
    ):
        response = await report_svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    create_report.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_report_actioned_does_not_change_report_count(mock_db):
    """Test B — PATCH /admin/reports leaves queue report_count unchanged."""
    post_id = uuid.uuid4()
    author_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    post = _post(state=PostState.published, author_user_id=author_id)
    post.id = post_id
    report = _report(entity_id=post_id)
    reporter_user = _user()
    reporter_profile = SimpleNamespace(first_name="Alice", last_name="Smith")
    row = (report, reporter_user, reporter_profile, None, None)

    db = mock_db()
    post_scalar = MagicMock()
    post_scalar.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_scalar)

    with (
        patch("apps.report.services.report_service.get_report_by_id", AsyncMock(return_value=row)),
        patch("apps.report.services.report_service._build_report_review_metadata", AsyncMock(return_value={})),
        patch("apps.report.services.report_service._apply_actioned_report_to_entity", AsyncMock(return_value=None)),
        patch("apps.report.services.report_service.update_report", AsyncMock(return_value=report)) as mock_update,
        patch(
            "apps.report.services.report_service.count_reports_by_entity_keys",
            AsyncMock(return_value={(ReportEntityType.post, post_id): 3}),
        ),
        patch("apps.report.services.report_service.get_previous_report_comments", AsyncMock(return_value=[])),
        patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", AsyncMock()),
        patch("apps.notifications.services.notify_post_author", AsyncMock()),
    ):
        response = await report_svc.review_report_admin_service(
            db,
            admin_id,
            ReportReviewRequest(
                report_id=report.id,
                status=ReportStatus.actioned,
                admin_comment="Flagged",
            ),
        )

    assert response.status is True
    mock_update.assert_awaited_once()
    assert response.data.report_count == 3


@pytest.mark.asyncio
async def test_admin_review_post_clears_queue_counts_without_deleting_reports(mock_db):
    """Test C — PATCH /admin/posts/reviewed clears queue count, keeps report rows."""
    post = _post(state=PostState.flagged)
    admin_id = uuid.uuid4()
    db = mock_db()

    post_result = MagicMock()
    post_result.scalar_one_or_none.return_value = post
    author_result = MagicMock()
    author_result.first.return_value = None
    db.execute = AsyncMock(side_effect=[post_result, author_result])

    with (
        patch.object(post_svc, "_create_revision", AsyncMock()),
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", AsyncMock()),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ) as clear_counts,
        patch("apps.profiles.services.profile_stats_service.increment_posts_count_for_user", AsyncMock()),
        patch("apps.notifications.services.notify_post_author", AsyncMock()),
    ):
        result = await post_svc.admin_publish_post_service(
            post.id,
            "published",
            admin_id,
            db,
        )

    assert result.state == PostState.published
    clear_counts.assert_awaited_once_with(
        db,
        entity_type=ReportEntityType.post,
        entity_id=post.id,
    )


@pytest.mark.asyncio
async def test_admin_change_user_status_clears_queue_counts_without_deleting_reports():
    """Test D — PATCH /users/{userId}/status clears queue count, keeps report rows."""
    from apps.administration.services import user_management_service as user_svc

    user_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    user = _user()
    user.id = user_id

    db = AsyncMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = user
    exec_result.first.return_value = None
    db.execute.return_value = exec_result

    with (
        patch.object(user_svc, "build_user_base_response", AsyncMock(return_value={"userId": str(user_id)})),
        patch(
            "apps.report.repositories.report_repository.clear_entity_report_queue_counts",
            AsyncMock(),
        ) as clear_counts,
        patch("apps.moderation.services.record_moderation_history", AsyncMock()),
        patch("apps.notifications.services.notify_account_status", AsyncMock()),
        patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", AsyncMock()),
        patch("core.auth.services.disable_firebase_user", lambda *_a, **_k: None),
    ):
        result = await user_svc.admin_update_user_status(
            str(user_id),
            AdminUserStatus.suspended,
            db,
            moderator_id=moderator_id,
            comment="Suspended",
        )

    assert result["status"] == "Suspended"
    clear_counts.assert_awaited_once_with(
        db,
        entity_type=ReportEntityType.user,
        entity_id=user_id,
    )


@pytest.mark.asyncio
async def test_review_report_never_clears_queue_counts_regression(mock_db):
    """Regression: PATCH /admin/reports must never clear queue counts."""
    post_id = uuid.uuid4()
    report = _report(entity_id=post_id)
    row = (report, _user(), SimpleNamespace(first_name="A", last_name="B"), None, None)
    db = mock_db()

    with (
        patch("apps.report.services.report_service.get_report_by_id", AsyncMock(return_value=row)),
        patch("apps.report.services.report_service._build_report_review_metadata", AsyncMock(return_value={})),
        patch("apps.report.services.report_service._apply_actioned_report_to_entity", AsyncMock(return_value=None)),
        patch("apps.report.services.report_service.update_report", AsyncMock(return_value=report)),
        patch(
            "apps.report.services.report_service.count_reports_by_entity_keys",
            AsyncMock(return_value={(ReportEntityType.post, post_id): 5}),
        ),
        patch("apps.report.services.report_service.get_previous_report_comments", AsyncMock(return_value=[])),
        patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", AsyncMock()),
        patch("apps.report.repositories.report_repository.clear_entity_report_queue_counts", AsyncMock()) as clear_counts,
        patch("apps.report.repositories.report_repository.hard_delete_reports_for_entity", AsyncMock()) as delete_reports,
    ):
        response = await report_svc.review_report_admin_service(
            db,
            uuid.uuid4(),
            ReportReviewRequest(
                report_id=report.id,
                status=ReportStatus.actioned,
            ),
        )

    assert response.status is True
    assert response.data.report_count == 5
    clear_counts.assert_not_called()
    delete_reports.assert_not_called()
