from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

from common.enums import PostState, ReportEntityType, ReportStatus, UserStatus
from apps.engagement.schemas import ReportCreateRequest, ReportReviewRequest
from apps.engagement.services import report_service as svc
from common.schemas import ApiResponse


def _user(is_deleted=False, deleted_at=None, status=UserStatus.active):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email="test@example.com",
        is_deleted=is_deleted,
        deleted_at=deleted_at,
        status=status,
    )


def _post(state=PostState.published):
    return SimpleNamespace(
        id=uuid.uuid4(),
        state=state,
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
    target_user = _user()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.user,
        entity_id=target_user.id,
        reason="Harassment",
    )

    db = mock_db(scalar_result(target_user), scalar_result(None))

    with patch.object(svc, "create_report", AsyncMock(return_value=_report())) as create_report:
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert response.message == "Report submitted successfully."
    create_report.assert_awaited_once()
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
    post = _post()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.post,
        entity_id=post.id,
        reason="Spam content",
    )
    db = mock_db(scalar_result(post), scalar_result(None))

    with patch.object(svc, "create_report", AsyncMock(return_value=_report())) as create_report:
        response = await svc.create_report_service(db, reporter_id, payload)

    assert response.status is True
    assert response.message == "Report submitted successfully."
    create_report.assert_awaited_once()


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
    comment = _comment()
    payload = ReportCreateRequest(
        entity_type=ReportEntityType.comment,
        entity_id=comment.id,
        reason="Abusive language",
    )
    db = mock_db(scalar_result(comment), scalar_result(None))

    with patch.object(svc, "create_report", AsyncMock(return_value=_report())) as create_report:
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
async def test_list_reports_admin_success(mock_db, scalar_result):
    report_obj = _report()
    reporter_user = _user()
    reporter_profile = SimpleNamespace(first_name="John", last_name="Doe")

    rows = [(report_obj, reporter_user, reporter_profile, None, None)]
    db = mock_db()

    with patch("apps.engagement.services.report_service.get_reports", AsyncMock(return_value=rows)), \
         patch("apps.engagement.services.report_service.count_reports", AsyncMock(return_value=1)):
        response = await svc.list_reports_admin_service(
            db,
            status=ReportStatus.under_review,
        )

    assert response.status is True
    assert len(response.data.items) == 1
    assert response.data.items[0].reason == "Spam"
    assert response.data.items[0].reporter_details.first_name == "John"


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

    with patch("apps.engagement.services.report_service.get_report_by_id", AsyncMock(side_effect=[row, updated_row])), \
         patch("apps.engagement.services.report_service.update_report", AsyncMock(return_value=report_obj)):
        response = await svc.review_report_admin_service(
            db,
            current_admin_id=admin_user.id,
            payload=payload,
        )

    assert response.status is True
    assert response.data.status == ReportStatus.under_review
    assert response.data.moderator_info.first_name == "Admin"
    db.commit.assert_awaited_once()
