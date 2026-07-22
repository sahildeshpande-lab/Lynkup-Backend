from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from common.enums import PostState, ReportEntityType, ReportStatus, UserStatus
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


def _post(state=PostState.published, moderator_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        state=state,
        moderator_id=moderator_id,
    )


def _comment(is_deleted=False):
    return SimpleNamespace(
        id=uuid.uuid4(),
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
        patch.object(svc, "create_report", AsyncMock(return_value=_report())) as create_report,
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert response.message == "Report submitted successfully."
    create_report.assert_awaited_once()
    assert create_report.await_args.kwargs["moderator_id"] == post_moderator_id


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
    db = mock_db(scalar_result(comment), scalar_result(None))

    with (
        patch.object(svc, "_resolve_report_moderator_id", AsyncMock(return_value=moderator_id)),
        patch.object(svc, "create_report", AsyncMock(return_value=_report())) as create_report,
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
async def test_create_report_allows_multiple_reports(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    post = _post()
    created = _report()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam",
    )
    db = mock_db(scalar_result(post))

    with patch(
        "apps.report.services.report_service._resolve_report_moderator_id",
        AsyncMock(return_value=None),
    ), patch(
        "apps.report.services.report_service.create_report",
        AsyncMock(return_value=created),
    ), patch(
        "apps.report.services.report_service._batch_moderator_names",
        AsyncMock(return_value={}),
    ):
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert response.message == "Report submitted successfully."


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
    report_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    queue_row = {
        "report_id": report_id,
        "entity_type": ReportEntityType.post,
        "entity_id": entity_id,
        "report_count": 3,
        "status": ReportStatus.under_review,
        "moderator_id": moderator_id,
        "admin_comment": None,
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
        "apps.report.services.report_service._load_entities_for_queue",
        AsyncMock(return_value={entity_id: entity_payload}),
    ), patch(
        "apps.report.services.report_service._batch_moderator_names",
        AsyncMock(return_value={moderator_id: "Mod Name"}),
    ), patch(
        "apps.report.services.report_service.get_previous_report_comments_for_entities",
        AsyncMock(return_value=[]),
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
    assert response.data.items[0].report_count == 3
    assert response.data.items[0].entity["caption"] == "Hello"
    assert response.data.items[0].status == ReportStatus.under_review
    assert response.data.items[0].admin_comment is None
    assert response.data.items[0].moderator_name == "Mod Name"
    assert response.data.items[0].previous_comments is None
    assert fetch_rows.await_args.kwargs["status"] == ReportStatus.under_review
    assert count_rows.await_args.kwargs["status"] == ReportStatus.under_review


@pytest.mark.asyncio
async def test_get_reported_entities_includes_previous_comments(mock_db):
    entity_id = uuid.uuid4()
    latest_report_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    queue_row = {
        "report_id": latest_report_id,
        "entity_type": ReportEntityType.post,
        "entity_id": entity_id,
        "report_count": 2,
        "status": ReportStatus.under_review,
        "moderator_id": moderator_id,
        "admin_comment": None,
        "latest_reported_at": now,
        "created_at": now,
        "updated_at": now,
    }
    previous = _report()
    previous.entity_id = entity_id
    previous.entity_type = ReportEntityType.post
    previous.status = ReportStatus.actioned
    previous.admin_comment = "Prior action taken"
    previous.moderator_id = uuid.uuid4()
    previous.updated_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    moderator_user = _user()
    moderator_profile = SimpleNamespace(first_name="Mod", last_name="One")
    db = mock_db()

    with patch(
        "apps.report.services.report_service.fetch_reported_entity_rows",
        AsyncMock(return_value=[queue_row]),
    ), patch(
        "apps.report.services.report_service.count_reported_entities",
        AsyncMock(return_value=1),
    ), patch(
        "apps.report.services.report_service._load_entities_for_queue",
        AsyncMock(return_value={entity_id: {"id": entity_id}}),
    ), patch(
        "apps.report.services.report_service._batch_moderator_names",
        AsyncMock(return_value={moderator_id: "Mod Name"}),
    ), patch(
        "apps.report.services.report_service.get_previous_report_comments_for_entities",
        AsyncMock(return_value=[(previous, moderator_user, moderator_profile)]),
    ) as previous_comments:
        response = await svc.get_reported_entities(
            db,
            entity_type=ReportEntityType.post,
            page=1,
            page_size=20,
            viewer_user_id=uuid.uuid4(),
        )

    assert response.status is True
    assert response.data.items[0].previous_comments is not None
    assert len(response.data.items[0].previous_comments) == 1
    assert response.data.items[0].previous_comments[0].admin_comment == "Prior action taken"
    assert response.data.items[0].previous_comments[0].moderator_name == "Mod One"
    previous_comments.assert_awaited_once_with(
        db,
        entity_type=ReportEntityType.post,
        entity_ids=[entity_id],
        exclude_report_ids=[latest_report_id],
    )


@pytest.mark.asyncio
async def test_get_report_details_includes_previous_comments(mock_db):
    current = _report()
    current.status = ReportStatus.under_review
    previous = _report()
    previous.status = ReportStatus.actioned
    previous.admin_comment = "Prior action taken"
    previous.moderator_id = uuid.uuid4()
    previous.updated_at = datetime(2026, 7, 1, tzinfo=timezone.utc)

    reporter_user = _user()
    reporter_profile = SimpleNamespace(first_name="John", last_name="Doe", profile_photo_url=None)
    moderator_user = _user()
    moderator_profile = SimpleNamespace(first_name="Mod", last_name="One")
    row = (current, reporter_user, reporter_profile, None, None)
    db = mock_db()

    with patch(
        "apps.report.services.report_service.get_report_by_id",
        AsyncMock(return_value=row),
    ), patch(
        "apps.report.services.report_service.count_reports_by_entity_keys",
        AsyncMock(return_value={(current.entity_type, current.entity_id): 2}),
    ), patch(
        "apps.report.services.report_service.get_previous_report_comments",
        AsyncMock(return_value=[(previous, moderator_user, moderator_profile)]),
    ) as previous_comments:
        response = await svc.get_report_details_admin_service(db, current.id)

    assert response.status is True
    assert response.data.previous_comments is not None
    assert len(response.data.previous_comments) == 1
    assert response.data.previous_comments[0].admin_comment == "Prior action taken"
    assert response.data.previous_comments[0].moderator_name == "Mod One"
    assert response.data.previous_comments[0].moderator_id == previous.moderator_id
    previous_comments.assert_awaited_once_with(
        db,
        entity_type=current.entity_type,
        entity_id=current.entity_id,
        exclude_report_id=current.id,
    )


@pytest.mark.asyncio
async def test_get_report_details_previous_comments_null_when_none(mock_db):
    current = _report()
    reporter_user = _user()
    reporter_profile = SimpleNamespace(first_name="John", last_name="Doe", profile_photo_url=None)
    row = (current, reporter_user, reporter_profile, None, None)
    db = mock_db()

    with patch(
        "apps.report.services.report_service.get_report_by_id",
        AsyncMock(return_value=row),
    ), patch(
        "apps.report.services.report_service.count_reports_by_entity_keys",
        AsyncMock(return_value={(current.entity_type, current.entity_id): 1}),
    ), patch(
        "apps.report.services.report_service.get_previous_report_comments",
        AsyncMock(return_value=[]),
    ):
        response = await svc.get_report_details_admin_service(db, current.id)

    assert response.status is True
    assert response.data.previous_comments is None


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
         patch("apps.report.services.report_service.count_reports_by_entity_keys", AsyncMock(return_value={(report_obj.entity_type, report_obj.entity_id): 1})), \
         patch("apps.report.services.report_service.get_previous_report_comments", AsyncMock(return_value=[])):
        response = await svc.review_report_admin_service(
            db,
            current_admin_id=admin_user.id,
            payload=payload,
        )

    assert response.status is True
    assert response.data.status == ReportStatus.under_review
    assert response.data.moderator_info.first_name == "Admin"
    assert response.data.moderator_name == "Admin User"
    assert response.data.previous_comments is None
    apply_action.assert_awaited_once()
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
         patch("apps.report.services.report_service.count_reports_by_entity_keys", AsyncMock(return_value={(report_obj.entity_type, report_obj.entity_id): 1})), \
         patch("apps.report.services.report_service.get_previous_report_comments", AsyncMock(return_value=[])):
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

    error = await svc._apply_actioned_report_to_entity(db, report, moderator_id=moderator_id)

    assert error is None
    assert post.state == PostState.flagged
    assert post.moderator_id == moderator_id
    assert post.is_moderator_reviewed is True
    db.add.assert_called()


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
        "apps.report.services.report_service.mark_comment_deleted",
        AsyncMock(return_value=comment),
    ) as mark_deleted:
        error = await svc._apply_actioned_report_to_entity(
            db,
            report,
            moderator_id=uuid.uuid4(),
        )

    assert error is None
    mark_deleted.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_actioned_report_blocks_user(mock_db, scalar_result):
    user = _user(status=UserStatus.active)
    user.firebase_uid = "firebase-uid"
    report = _report()
    report.entity_type = ReportEntityType.user
    report.entity_id = user.id
    db = mock_db(scalar_result(user))

    with patch("core.auth.services.disable_firebase_user") as disable_mock:
        error = await svc._apply_actioned_report_to_entity(
            db,
            report,
            moderator_id=uuid.uuid4(),
        )

    assert error is None
    assert user.status == UserStatus.suspended
    db.add.assert_called()
    disable_mock.assert_called_once_with("firebase-uid")


@pytest.mark.asyncio
async def test_get_reported_entities_includes_admin_comment(mock_db):
    entity_id = uuid.uuid4()
    moderator_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    queue_row = {
        "report_id": uuid.uuid4(),
        "entity_type": ReportEntityType.user,
        "entity_id": entity_id,
        "report_count": 2,
        "status": ReportStatus.actioned,
        "moderator_id": moderator_id,
        "admin_comment": "Harassment confirmed",
        "latest_reported_at": now,
        "created_at": now,
        "updated_at": now,
    }
    db = mock_db()

    with patch(
        "apps.report.services.report_service.fetch_reported_entity_rows",
        AsyncMock(return_value=[queue_row]),
    ), patch(
        "apps.report.services.report_service.count_reported_entities",
        AsyncMock(return_value=1),
    ), patch(
        "apps.report.services.report_service._load_entities_for_queue",
        AsyncMock(return_value={entity_id: {"id": str(entity_id), "status": "suspended"}}),
    ), patch(
        "apps.report.services.report_service._batch_moderator_names",
        AsyncMock(return_value={moderator_id: "Mod Name"}),
    ), patch(
        "apps.report.services.report_service.get_previous_report_comments_for_entities",
        AsyncMock(return_value=[]),
    ):
        response = await svc.get_reported_entities(
            db,
            entity_type=ReportEntityType.user,
            status=ReportStatus.actioned,
            page=1,
            page_size=20,
            viewer_user_id=uuid.uuid4(),
        )

    assert response.status is True
    assert response.data.items[0].admin_comment == "Harassment confirmed"
    assert response.data.items[0].status == ReportStatus.actioned
    assert response.data.items[0].is_reviewed is True
    assert response.data.items[0].previous_comments is None
