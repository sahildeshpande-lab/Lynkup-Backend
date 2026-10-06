from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest

from common.enums import (
    PostState,
    ReportEntityType,
    ReportStatus,
    ReportedEntityOrder,
    ReportedEntitySort,
    UserStatus,
)
from apps.report.schemas import ReportCreateRequest, ReportReviewRequest
from apps.report.services import report_service as svc


def _user(is_deleted=False, deleted_at=None, status=UserStatus.active):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="test@example.com",
        is_deleted=is_deleted,
        deleted_at=deleted_at,
        status=status,
    )


def _post(state=PostState.published, moderator_id=None, author_user_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        author_user_id=author_user_id or uuid.uuid4(),
        state=state,
        moderator_id=moderator_id,
        is_moderator_reviewed=False,
        reviewed_at=None,
        updated_at=None,
    )


def _comment(is_deleted=False, *, parent_comment_id=None, post_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        post_id=post_id or uuid.uuid4(),
        parent_comment_id=parent_comment_id,
        is_deleted=is_deleted,
    )


def _report():
    return SimpleNamespace(
        id=uuid.uuid4(),
        reported_id=uuid.uuid4(),
        entity_type=ReportEntityType.post,
        entity_id=uuid.uuid4(),
        reason="Spam",
        status=ReportStatus.under_review,
        moderator_id=None,
        admin_comment=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_create_report_user_success(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    target_user = _user()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.user,
        entity_id=target_user.id,
        reason="Harassment",
    )

    db = mock_db(scalar_result(target_user), scalar_result(None))

    with (
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=moderator_id)),
        patch.object(svc, "create_report", AsyncMock(return_value=_report())) as create_report,
        patch.object(svc, "_apply_user_report_threshold", AsyncMock(return_value=False)),
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert response.message == "Report submitted successfully."
    create_report.assert_awaited_once()
    assert create_report.await_args.kwargs["moderator_id"] == moderator_id
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_report_self_report_prevention(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.user,
        entity_id=reporter_id,
        reason="Self report",
    )
    db = mock_db()

    response = await svc.create_report_service(db, reporter_id, payload)
    assert response.status is False
    assert "cannot report yourself" in response.message.lower()


@pytest.mark.asyncio
async def test_create_report_soft_deleted_user(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    target_user = _user(is_deleted=True)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.user,
        entity_id=target_user.id,
        reason="Harassment",
    )
    db = mock_db(scalar_result(target_user))

    response = await svc.create_report_service(db, reporter_id, payload)
    assert response.status is False
    assert "soft-deleted user" in response.message.lower()


@pytest.mark.asyncio
async def test_create_report_post_success(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    post_moderator_id = uuid.uuid4()
    post = _post(moderator_id=post_moderator_id)
    revision = SimpleNamespace(id=uuid.uuid4())
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam content",
    )
    db = mock_db(scalar_result(post), scalar_result(None))

    with (
        patch.object(
            svc,
            "_resolve_report_moderator_id",
            AsyncMock(return_value=post_moderator_id),
        ),
        patch.object(
            svc,
            "get_latest_post_revision",
            AsyncMock(return_value=revision),
        ),
        patch.object(svc, "create_report", AsyncMock(return_value=_report())) as create_report,
        patch.object(svc, "_apply_post_report_threshold", AsyncMock(return_value=False)),
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert response.message == "Report submitted successfully."
    create_report.assert_awaited_once()
    assert create_report.await_args.kwargs["moderator_id"] == post_moderator_id
    assert create_report.await_args.kwargs["post_revision_id"] == revision.id


@pytest.mark.asyncio
async def test_create_report_soft_deleted_post(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    post = _post(state=PostState.deleted)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam content",
    )
    db = mock_db(scalar_result(post))

    response = await svc.create_report_service(db, reporter_id, payload)
    assert response.status is False
    assert "soft-deleted post" in response.message.lower()


@pytest.mark.asyncio
async def test_create_report_comment_success(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    comment = _comment()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.comment,
        entity_id=comment.id,
        reason="Abusive language",
    )
    db = mock_db(scalar_result(comment), scalar_result(None), scalar_result(None))

    with (
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=moderator_id)),
        patch.object(svc, "create_report", AsyncMock(return_value=_report())) as create_report,
        patch.object(svc, "_apply_comment_report_threshold", AsyncMock(return_value=False)),
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert response.message == "Report submitted successfully."
    create_report.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_report_soft_deleted_comment(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    comment = _comment(is_deleted=True)
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.comment,
        entity_id=comment.id,
        reason="Abusive language",
    )
    db = mock_db(scalar_result(comment))

    response = await svc.create_report_service(db, reporter_id, payload)
    assert response.status is False
    assert "soft-deleted comment" in response.message.lower()


@pytest.mark.asyncio
async def test_create_report_duplicate_prevention(mock_db, scalar_result):
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
async def test_create_report_post_requires_revision(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    post = _post()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam content",
    )
    db = mock_db(scalar_result(post))

    with patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=None)):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is False
    assert "revision not found" in response.message.lower()

@pytest.mark.asyncio
async def test_create_report_invalid_entity(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=uuid.uuid4(),
        reason="Spam",
    )
    db = mock_db(scalar_result(None))

    response = await svc.create_report_service(db, reporter_id, payload)
    assert response.status is False
    assert "post does not exist" in response.message.lower()


@pytest.mark.asyncio
async def test_get_reports_for_entity_success(mock_db):
    report_obj = _report()
    reporter_user = _user()
    reporter_profile = SimpleNamespace(first_name="John", last_name="Doe")
    rows = [(report_obj, reporter_user, reporter_profile, None, None)]
    db = mock_db()

    with patch(
        "apps.report.services.report_service.fetch_report_rows",
        AsyncMock(return_value=rows),
    ), patch(
        "apps.report.services.report_service.count_report_rows",
        AsyncMock(return_value=1),
    ):
        response = await svc.get_reports(
            db,
            entity_type=ReportEntityType.post,
            entity_id=report_obj.entity_id,
        )

    assert response.status is True
    assert len(response.data.items) == 1
    assert response.data.items[0].who_reported_id == report_obj.reported_id
    assert response.data.items[0].reason == "Spam"
    assert response.data.items[0].reporter_details.first_name == "John"


@pytest.mark.asyncio
async def test_get_reported_entities_success(mock_db):
    entity_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    queue_row = {
        "entity_type": ReportEntityType.post,
        "entity_id": entity_id,
        "report_count": 3,
        "status": ReportStatus.under_review,
        "moderator_id": moderator_id,
        "latest_reported_at": now,
        "created_at": now,
        "updated_at": now,
    }
    entity_payload = {"id": entity_id, "caption": "Hello"}
    db = mock_db()
    viewer_id = uuid.uuid4()

    with patch(
        "apps.report.services.report_service.fetch_reported_entity_rows",
        AsyncMock(return_value=[queue_row]),
    ) as fetch_rows, patch(
        "apps.report.services.report_service.count_reported_entities",
        AsyncMock(return_value=1),
    ) as count_rows, patch(
        "apps.report.services.report_service.count_reported_entities_summary_by_status",
        AsyncMock(
            return_value={
                "under_review": 1,
                "actioned": 4,
                "rejected": 2,
            }
        ),
    ) as summary_rows, patch(
        "apps.report.services.report_service._load_entities_for_queue",
        AsyncMock(return_value={entity_id: entity_payload}),
    ), patch(
        "apps.report.services.report_service._batch_moderator_names",
        AsyncMock(return_value={moderator_id: "Mod Name"}),
    ):
        response = await svc.get_reported_entities(
            db,
            entity_type=ReportEntityType.post,
            status=ReportStatus.under_review,
            page=1,
            page_size=20,
            viewer_user_id=viewer_id,
        )

    assert response.status is True
    assert response.message == "Reported entities fetched successfully."
    assert response.data.totalItems == 1
    assert response.data.summary.under_review == 1
    assert response.data.summary.actioned == 4
    assert response.data.summary.rejected == 2
    assert response.data.total == 7
    assert response.data.items[0].report_count == 3
    assert response.data.items[0].entity["caption"] == "Hello"
    assert response.data.items[0].status == ReportStatus.under_review
    assert response.data.items[0].moderator_name == "Mod Name"
    assert fetch_rows.await_args.kwargs["status"] == ReportStatus.under_review
    assert fetch_rows.await_args.kwargs["sort"] == ReportedEntitySort.report_count
    assert fetch_rows.await_args.kwargs["order"] == ReportedEntityOrder.desc
    assert count_rows.await_args.kwargs["status"] == ReportStatus.under_review
    summary_rows.assert_awaited_once()
    assert summary_rows.await_args.kwargs["entity_type"] == ReportEntityType.post


@pytest.mark.asyncio
async def test_get_reported_entities_sorts_by_latest_reported_at(mock_db):
    db = mock_db()
    viewer_id = uuid.uuid4()

    with patch(
        "apps.report.services.report_service.fetch_reported_entity_rows",
        AsyncMock(return_value=[]),
    ) as fetch_rows, patch(
        "apps.report.services.report_service.count_reported_entities",
        AsyncMock(return_value=0),
    ), patch(
        "apps.report.services.report_service.count_reported_entities_summary_by_status",
        AsyncMock(
            return_value={
                "under_review": 0,
                "actioned": 0,
                "rejected": 0,
            }
        ),
    ), patch(
        "apps.report.services.report_service._load_entities_for_queue",
        AsyncMock(return_value={}),
    ), patch(
        "apps.report.services.report_service._batch_moderator_names",
        AsyncMock(return_value={}),
    ):
        response = await svc.get_reported_entities(
            db,
            entity_type=ReportEntityType.post,
            page=1,
            page_size=20,
            viewer_user_id=viewer_id,
            sort=ReportedEntitySort.latest_reported_at,
        )

    assert response.status is True
    assert fetch_rows.await_args.kwargs["sort"] == ReportedEntitySort.latest_reported_at
    assert fetch_rows.await_args.kwargs["order"] == ReportedEntityOrder.desc


@pytest.mark.asyncio
async def test_get_reported_entities_sorts_by_created_at_asc(mock_db):
    db = mock_db()
    viewer_id = uuid.uuid4()

    with patch(
        "apps.report.services.report_service.fetch_reported_entity_rows",
        AsyncMock(return_value=[]),
    ) as fetch_rows, patch(
        "apps.report.services.report_service.count_reported_entities",
        AsyncMock(return_value=0),
    ), patch(
        "apps.report.services.report_service.count_reported_entities_summary_by_status",
        AsyncMock(
            return_value={
                "under_review": 0,
                "actioned": 0,
                "rejected": 0,
            }
        ),
    ), patch(
        "apps.report.services.report_service._load_entities_for_queue",
        AsyncMock(return_value={}),
    ), patch(
        "apps.report.services.report_service._batch_moderator_names",
        AsyncMock(return_value={}),
    ):
        response = await svc.get_reported_entities(
            db,
            entity_type=ReportEntityType.post,
            page=1,
            page_size=20,
            viewer_user_id=viewer_id,
            sort=ReportedEntitySort.created_at,
            order=ReportedEntityOrder.asc,
        )

    assert response.status is True
    assert fetch_rows.await_args.kwargs["sort"] == ReportedEntitySort.created_at
    assert fetch_rows.await_args.kwargs["order"] == ReportedEntityOrder.asc


@pytest.mark.asyncio
async def test_get_reported_entities_forwards_search(mock_db):
    db = mock_db()
    viewer_id = uuid.uuid4()

    with patch(
        "apps.report.services.report_service.fetch_reported_entity_rows",
        AsyncMock(return_value=[]),
    ) as fetch_rows, patch(
        "apps.report.services.report_service.count_reported_entities",
        AsyncMock(return_value=0),
    ) as count_rows, patch(
        "apps.report.services.report_service.count_reported_entities_summary_by_status",
        AsyncMock(return_value={"under_review": 0, "actioned": 0, "rejected": 0}),
    ), patch(
        "apps.report.services.report_service._load_entities_for_queue",
        AsyncMock(return_value={}),
    ), patch(
        "apps.report.services.report_service._batch_moderator_names",
        AsyncMock(return_value={}),
    ):
        await svc.get_reported_entities(
            db,
            entity_type=ReportEntityType.comment,
            page=1,
            page_size=9,
            viewer_user_id=viewer_id,
            search="jane",
        )

    assert fetch_rows.await_args.kwargs["search"] == "jane"
    assert count_rows.await_args.kwargs["search"] == "jane"


def test_reported_entity_search_clause_is_none_for_blank():
    from apps.report.repositories.report_repository import _reported_entity_search_clause

    assert _reported_entity_search_clause(ReportEntityType.post, None) is None
    assert _reported_entity_search_clause(ReportEntityType.post, "  ") is None
    assert _reported_entity_search_clause(None, "jane") is None


def test_reported_entity_search_clause_post_matches_name_and_content():
    from sqlalchemy.dialects import postgresql

    from apps.report.repositories.report_repository import _reported_entity_search_clause

    clause = _reported_entity_search_clause(ReportEntityType.post, "jane")
    sql = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "ilike" in sql
    assert "%jane%" in sql
    assert "first_name" in sql
    assert "last_name" in sql
    assert "caption" in sql
    assert "content_html" in sql


def test_reported_entity_search_clause_user_matches_name_and_university():
    from sqlalchemy.dialects import postgresql

    from apps.report.repositories.report_repository import _reported_entity_search_clause

    clause = _reported_entity_search_clause(ReportEntityType.user, "mit")
    sql = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "ilike" in sql
    assert "%mit%" in sql
    assert "first_name" in sql
    assert "last_name" in sql
    assert "university" in sql or "name" in sql


def test_reported_entity_search_clause_comment_matches_name_and_text():
    from sqlalchemy.dialects import postgresql

    from apps.report.repositories.report_repository import _reported_entity_search_clause

    clause = _reported_entity_search_clause(ReportEntityType.comment, "spam")
    sql = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "ilike" in sql
    assert "%spam%" in sql
    assert "first_name" in sql
    assert "last_name" in sql
    assert "comment_text" in sql


@pytest.mark.asyncio
async def test_review_report_admin_success(mock_db, scalar_result):
    report_obj = _report()
    reporter_user = _user()
    reporter_profile = SimpleNamespace(first_name="John", last_name="Doe")
    admin_user = _user(status=UserStatus.active)
    admin_profile = SimpleNamespace(first_name="Admin", last_name="User")

    row = (report_obj, reporter_user, reporter_profile, None, None)
    updated_row = (report_obj, reporter_user, reporter_profile, admin_user, admin_profile)

    db = mock_db()
    payload = ReportReviewRequest(report_id=report_obj.id, status=ReportStatus.actioned, admin_comment="Resolved")

    with patch("apps.report.services.report_service.get_report_by_id", AsyncMock(side_effect=[row, updated_row])), \
         patch("apps.report.services.report_service.update_report", AsyncMock(return_value=report_obj)), \
         patch("apps.report.services.report_service._apply_actioned_report_to_entity", AsyncMock(return_value=None)) as apply_action, \
         patch("apps.report.services.report_service.count_reports_by_entity_keys", AsyncMock(return_value={(report_obj.entity_type, report_obj.entity_id): 1})):
        response = await svc.review_report_admin_service(
            db,
            current_admin_id=admin_user.id,
            payload=payload,
        )

    assert response.status is True
    assert response.data.status == ReportStatus.under_review
    assert response.data.moderator_info.first_name == "Admin"
    assert response.data.moderator_name == "Admin User"
    apply_action.assert_awaited_once()
    assert apply_action.await_args.kwargs["admin_comment"] == "Resolved"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_report_rejected_skips_entity_action(mock_db):
    report_obj = _report()
    reporter_user = _user()
    reporter_profile = SimpleNamespace(first_name="John", last_name="Doe")
    admin_user = _user(status=UserStatus.active)
    admin_profile = SimpleNamespace(first_name="Admin", last_name="User")

    row = (report_obj, reporter_user, reporter_profile, None, None)
    updated_row = (report_obj, reporter_user, reporter_profile, admin_user, admin_profile)
    db = mock_db()
    payload = ReportReviewRequest(
        report_id=report_obj.id,
        status=ReportStatus.rejected,
        admin_comment="Not a violation",
    )

    with patch("apps.report.services.report_service.get_report_by_id", AsyncMock(side_effect=[row, updated_row])), \
         patch("apps.report.services.report_service.update_report", AsyncMock(return_value=report_obj)), \
         patch("apps.report.services.report_service._apply_actioned_report_to_entity", AsyncMock(return_value=None)) as apply_action, \
         patch("apps.report.services.report_service.count_reports_by_entity_keys", AsyncMock(return_value={(report_obj.entity_type, report_obj.entity_id): 1})):
        response = await svc.review_report_admin_service(
            db,
            current_admin_id=admin_user.id,
            payload=payload,
        )

    assert response.status is True
    apply_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_actioned_report_flags_post(mock_db, scalar_result):
    post = _post(state=PostState.published)
    report = _report()
    report.entity_type = ReportEntityType.post
    report.entity_id = post.id
    db = mock_db(scalar_result(post))
    moderator_id = uuid.uuid4()

    with patch(
        "apps.profiles.services.profile_stats_service.sync_posts_count_for_visibility_change",
        AsyncMock(),
    ) as sync_count, patch(
        "apps.moderation.services.record_moderation_history",
        AsyncMock(),
    ) as history:
        error = await svc._apply_actioned_report_to_entity(
            db,
            report,
            moderator_id=moderator_id,
            admin_comment="  Action taken on the post  ",
        )

    assert error is None
    assert post.state == PostState.flagged
    assert post.moderator_id == moderator_id
    assert post.is_moderator_reviewed is True
    sync_count.assert_awaited_once_with(
        db,
        post_id=post.id,
        author_user_id=post.author_user_id,
        was_counted=True,
        now_counted=False,
    )
    history.assert_awaited_once()
    assert history.await_args.kwargs["comment"] == "Action taken on the post"
    db.add.assert_called()


@pytest.mark.asyncio
async def test_apply_actioned_report_flags_post_skips_decrement_when_already_flagged(
    mock_db, scalar_result
):
    post = _post(state=PostState.flagged)
    report = _report()
    report.entity_type = ReportEntityType.post
    report.entity_id = post.id
    db = mock_db(scalar_result(post))
    moderator_id = uuid.uuid4()

    with patch(
        "apps.profiles.services.profile_stats_service.sync_posts_count_for_visibility_change",
        AsyncMock(),
    ) as sync_count:
        error = await svc._apply_actioned_report_to_entity(db, report, moderator_id=moderator_id)

    assert error is None
    assert post.state == PostState.flagged
    sync_count.assert_not_called()


@pytest.mark.asyncio
async def test_apply_actioned_report_flags_post_skips_decrement_for_draft(mock_db, scalar_result):
    post = _post(state=PostState.draft)
    report = _report()
    report.entity_type = ReportEntityType.post
    report.entity_id = post.id
    db = mock_db(scalar_result(post))
    moderator_id = uuid.uuid4()

    with patch(
        "apps.profiles.services.profile_stats_service.sync_posts_count_for_visibility_change",
        AsyncMock(),
    ) as sync_count:
        error = await svc._apply_actioned_report_to_entity(db, report, moderator_id=moderator_id)

    assert error is None
    assert post.state == PostState.flagged
    sync_count.assert_not_called()


@pytest.mark.asyncio
async def test_apply_actioned_report_deletes_comment(mock_db):
    comment = _comment()
    report = _report()
    report.entity_type = ReportEntityType.comment
    report.entity_id = comment.id
    db = mock_db()

    with patch(
        "apps.report.services.report_service.get_comment_by_id",
        AsyncMock(return_value=comment),
    ), patch(
        "apps.report.services.report_service.update_post_comment_count",
        AsyncMock(return_value=0),
    ) as update_count, patch(
        "apps.report.services.report_service.mark_comment_deleted",
        AsyncMock(return_value=comment),
    ) as mark_deleted:
        error = await svc._apply_actioned_report_to_entity(
            db,
            report,
            moderator_id=uuid.uuid4(),
        )

    assert error is None
    update_count.assert_awaited_once_with(db, comment.post_id, -1)
    mark_deleted.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_actioned_report_reply_does_not_decrement_comment_count(mock_db):
    comment = _comment(parent_comment_id=uuid.uuid4())
    report = _report()
    report.entity_type = ReportEntityType.comment
    report.entity_id = comment.id
    db = mock_db()

    with patch(
        "apps.report.services.report_service.get_comment_by_id",
        AsyncMock(return_value=comment),
    ), patch(
        "apps.report.services.report_service.update_post_comment_count",
        AsyncMock(return_value=0),
    ) as update_count, patch(
        "apps.report.services.report_service.mark_comment_deleted",
        AsyncMock(return_value=comment),
    ) as mark_deleted:
        error = await svc._apply_actioned_report_to_entity(
            db,
            report,
            moderator_id=uuid.uuid4(),
        )

    assert error is None
    update_count.assert_not_awaited()
    mark_deleted.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_actioned_report_skips_already_deleted_comment(mock_db):
    comment = _comment(is_deleted=True)
    report = _report()
    report.entity_type = ReportEntityType.comment
    report.entity_id = comment.id
    db = mock_db()

    with patch(
        "apps.report.services.report_service.get_comment_by_id",
        AsyncMock(return_value=comment),
    ), patch(
        "apps.report.services.report_service.update_post_comment_count",
        AsyncMock(return_value=0),
    ) as update_count, patch(
        "apps.report.services.report_service.mark_comment_deleted",
        AsyncMock(return_value=comment),
    ) as mark_deleted:
        error = await svc._apply_actioned_report_to_entity(
            db,
            report,
            moderator_id=uuid.uuid4(),
        )

    assert error is None
    update_count.assert_not_awaited()
    mark_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_report_fallback_to_entity_id(mock_db):
    report_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    report = _report()
    report.id = report_id
    report.entity_id = entity_id
    reporter_user = _user()
    reporter_profile = SimpleNamespace(first_name="John", last_name="Doe")
    row = (report, reporter_user, reporter_profile, None, None)

    db = mock_db()

    with patch(
        "apps.report.services.report_service.get_report_by_id",
        AsyncMock(side_effect=[None, row, row])
    ) as mock_get_by_id, patch(
        "apps.report.services.report_service._build_report_review_metadata",
        AsyncMock(return_value={})
    ), patch(
        "apps.report.services.report_service.update_report",
        AsyncMock()
    ) as mock_update, patch(
        "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
        AsyncMock()
    ), patch(
        "apps.report.services.report_service.count_reports_by_entity_keys",
        AsyncMock(return_value={})
    ), patch(
        "apps.report.services.report_service.get_previous_report_comments",
        AsyncMock(return_value=[])
    ):
        db_execute_result = MagicMock()
        db_execute_result.scalars.return_value.all.return_value = [report]
        db.execute = AsyncMock(return_value=db_execute_result)

        payload = ReportReviewRequest(
            report_id=entity_id,
            status=ReportStatus.rejected,
            admin_comment="Verified",
        )
        response = await svc.review_report_admin_service(
            db,
            uuid.uuid4(),
            payload,
            actor_role="superadmin"
        )

    assert response.status is True
    assert response.message == "Report reviewed successfully"
    assert mock_get_by_id.call_count == 3
    assert mock_get_by_id.call_args_list[0][0][1] == entity_id
    assert mock_get_by_id.call_args_list[1][0][1] == report.id
    assert mock_get_by_id.call_args_list[2][0][1] == report.id


@pytest.mark.asyncio
async def test_review_report_invalid_report_id_returns_soft_error(mock_db):
    db = mock_db()
    db_execute_result = MagicMock()
    db_execute_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=db_execute_result)

    payload = ReportReviewRequest(
        report_id=uuid.uuid4(),
        status=ReportStatus.actioned,
        admin_comment="action on post",
    )

    with patch(
        "apps.report.services.report_service.get_report_by_id",
        AsyncMock(return_value=None),
    ):
        response = await svc.review_report_admin_service(
            db,
            current_admin_id=uuid.uuid4(),
            payload=payload,
        )

    assert response.status is False
    assert response.message == "Inappropiate report id"
    assert response.data is None


@pytest.mark.asyncio
async def test_review_report_actions_single_report_and_notifies_author_once(mock_db):
    post_id = uuid.uuid4()
    author_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    post = _post(state=PostState.published, author_user_id=author_id)
    post.id = post_id

    report1 = _report()
    report1.entity_id = post_id
    reporter_user = _user()
    reporter_profile = SimpleNamespace(first_name="Alice", last_name="Smith")
    row = (report1, reporter_user, reporter_profile, None, None)

    db = mock_db()
    # Mock db.execute to return post
    post_scalar = MagicMock()
    post_scalar.scalar_one_or_none.return_value = post
    db.execute = AsyncMock(return_value=post_scalar)

    with (
        patch("apps.report.services.report_service.get_report_by_id", AsyncMock(return_value=row)),
        patch("apps.report.services.report_service._build_report_review_metadata", AsyncMock(return_value={})),
        patch("apps.report.services.report_service._apply_actioned_report_to_entity", AsyncMock(return_value=None)),
        patch("apps.report.services.report_service.update_report", AsyncMock(return_value=report1)) as mock_update,
        patch("apps.administration.services.admin_activity_log_service.create_admin_activity_log", AsyncMock()) as mock_activity_log,
        patch("apps.notifications.services.notify_post_author", AsyncMock()) as mock_notify,
        patch(
            "apps.report.services.report_service.count_reports_by_entity_keys",
            AsyncMock(return_value={(ReportEntityType.post, post_id): 3}),
        ),
        patch("apps.report.services.report_service.get_previous_report_comments", AsyncMock(return_value=[])),
    ):
        payload = ReportReviewRequest(
            report_id=report1.id,
            status=ReportStatus.actioned,
            admin_comment="Flagged inappropriate content",
        )
        response = await svc.review_report_admin_service(
            db,
            admin_id,
            payload,
            actor_role="moderator",
        )

    assert response.status is True
    mock_update.assert_awaited_once_with(
        db,
        report_id=report1.id,
        status=ReportStatus.actioned,
        admin_comment="Flagged inappropriate content",
        moderator_id=admin_id,
    )
    # Verify exactly 1 admin activity log was created
    mock_activity_log.assert_awaited_once()
    # Verify exactly 1 notification was sent to author
    mock_notify.assert_awaited_once_with(
        db,
        post_id=post_id,
        author_user_id=author_id,
        notification_type="POST_FLAGGED",
    )


@pytest.mark.asyncio
async def test_resolve_report_moderator_reuses_open_report_moderator(mock_db, scalar_result):
    entity_id = uuid.uuid4()
    existing_mod_id = uuid.uuid4()
    db = mock_db(scalar_result(existing_mod_id))

    mod_id = await svc._resolve_report_moderator_id(
        db,
        entity_type=ReportEntityType.user,
        entity_id=entity_id,
    )

    assert mod_id == existing_mod_id


@pytest.mark.asyncio
async def test_resolve_report_moderator_assigns_round_robin_when_no_open_report(mock_db, scalar_result):
    entity_id = uuid.uuid4()
    assigned_mod_id = uuid.uuid4()
    db = mock_db(scalar_result(None))

    with patch(
        "apps.report.services.report_service.assign_next_moderator_round_robin",
        AsyncMock(return_value=assigned_mod_id),
    ) as mock_rr:
        mod_id = await svc._resolve_report_moderator_id(
            db,
            entity_type=ReportEntityType.user,
            entity_id=entity_id,
        )

    assert mod_id == assigned_mod_id
    mock_rr.assert_awaited_once_with(db)


@pytest.mark.asyncio
async def test_create_report_on_repost_resolves_to_original_post(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    repost_id = uuid.uuid4()
    original_post = _post()
    repost = SimpleNamespace(id=repost_id, post_id=original_post.id, is_deleted=False)
    revision = SimpleNamespace(id=uuid.uuid4(), post_id=original_post.id)
    moderator_id = uuid.uuid4()

    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=repost_id,
        reason="Inappropriate Content",
    )

    # 1. Post lookup by repost_id -> None
    # 2. Repost lookup by repost_id -> repost
    # 3. Post lookup by repost.post_id -> original_post
    # 4. Duplicate report check -> None
    db = mock_db(
        scalar_result(None),
        scalar_result(repost),
        scalar_result(original_post),
        scalar_result(None),
    )

    with (
        patch.object(svc, "get_latest_post_revision", AsyncMock(return_value=revision)),
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=moderator_id)),
        patch.object(svc, "create_report", AsyncMock(return_value=_report())) as mock_create_report,
        patch.object(svc, "_apply_post_report_threshold", AsyncMock(return_value=False)),
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert response.message == "Report submitted successfully."
    mock_create_report.assert_awaited_once()
    assert mock_create_report.await_args.kwargs["entity_id"] == original_post.id
    assert mock_create_report.await_args.kwargs["entity_type"] == ReportEntityType.post
    assert mock_create_report.await_args.kwargs["post_revision_id"] == revision.id
    db.commit.assert_awaited_once()




