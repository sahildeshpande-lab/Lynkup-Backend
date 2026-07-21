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
async def test_create_report_duplicate_prevention(mock_db, scalar_result):
    reporter_id = uuid.uuid4()
    post = _post()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam",
    )
    db = mock_db(scalar_result(post), scalar_result(_report()))

    response = await svc.create_report_service(db, reporter_id, payload)
    assert response.status is False
    assert "already reported" in response.message.lower()


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
    now = datetime.now(timezone.utc)
    queue_row = {
        "entity_type": ReportEntityType.post,
        "entity_id": entity_id,
        "report_count": 3,
        "status": ReportStatus.under_review,
        "moderator_id": uuid.uuid4(),
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
    ), patch(
        "apps.report.services.report_service.count_reported_entities",
        AsyncMock(return_value=1),
    ), patch(
        "apps.report.services.report_service._load_entities_for_queue",
        AsyncMock(return_value={entity_id: entity_payload}),
    ):
        response = await svc.get_reported_entities(
            db,
            entity_type=ReportEntityType.post,
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
         patch("apps.report.services.report_service.count_reports_by_entity_keys", AsyncMock(return_value={(report_obj.entity_type, report_obj.entity_id): 1})):
        response = await svc.review_report_admin_service(
            db,
            current_admin_id=admin_user.id,
            payload=payload,
        )

    assert response.status is True
    assert response.data.status == ReportStatus.under_review
    assert response.data.moderator_info.first_name == "Admin"
    db.commit.assert_awaited_once()
