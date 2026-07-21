from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from fastapi import status
from fastapi.testclient import TestClient

from entrypoints.api import app
from core.database.session import get_session
from core.security.auth import get_current_app_user, get_current_moderator
from apps.accounts.db_models import User
from common.schemas import ApiResponse
from apps.report.schemas import (
    ReportListResponse,
    ReportResponse,
    ReportedEntityListResponse,
)

client = TestClient(app)

_MOCK_USER_ID = uuid.uuid4()
_MOCK_ADMIN_ID = uuid.uuid4()
_MOCK_ENTITY_ID = uuid.uuid4()


class _NoopSession:
    pass


async def _override_session():
    yield _NoopSession()


async def _mock_current_user():
    user = User(id=_MOCK_USER_ID, email="user@example.com")
    user.role = "user"
    return user


async def _mock_current_moderator():
    user = User(id=_MOCK_ADMIN_ID, email="moderator@example.com")
    user.role = "moderator"
    return user


def setup_module() -> None:
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_current_app_user] = _mock_current_user
    app.dependency_overrides[get_current_moderator] = _mock_current_moderator


def teardown_module() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_current_app_user, None)
    app.dependency_overrides.pop(get_current_moderator, None)


def test_post_report_success():
    payload = {
        "entity_type": "post",
        "entity_id": str(uuid.uuid4()),
        "reason": "Inappropriate post content",
    }

    mock_response = ApiResponse(status=True, message="Report submitted successfully.", data={})

    with patch("apps.report.routes.create_report_service", AsyncMock(return_value=mock_response)) as create_svc:
        response = client.post("/api/v1/reports", json=payload)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] is True
    assert response.json()["message"] == "Report submitted successfully."
    create_svc.assert_awaited_once()


def test_get_reports_admin_success():
    mock_data = {
        "items": [],
        "page": 1,
        "pageSize": 20,
        "totalItems": 0,
        "totalPages": 0,
    }
    mock_response = ReportListResponse(
        status=True,
        message="Reports retrieved successfully.",
        data=mock_data,
    )

    with patch("apps.report.routes.get_reports", AsyncMock(return_value=mock_response)) as list_svc:
        response = client.get(
            "/api/v1/admin/reports",
            params={
                "entity_type": "post",
                "entity_id": str(_MOCK_ENTITY_ID),
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] is True
    list_svc.assert_awaited_once()


def test_get_reported_entities_admin_success():
    mock_data = {
        "items": [],
        "page": 1,
        "pageSize": 20,
        "totalItems": 0,
        "totalPages": 0,
    }
    mock_response = ReportedEntityListResponse(
        status=True,
        message="Reported entities fetched successfully.",
        data=mock_data,
    )

    with patch(
        "apps.report.routes.get_reported_entities",
        AsyncMock(return_value=mock_response),
    ) as detail_svc:
        response = client.get(
            "/api/v1/admin/reports/details",
            params={"entity_type": "post"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] is True
    assert response.json()["message"] == "Reported entities fetched successfully."
    detail_svc.assert_awaited_once()


def test_get_report_by_id_admin_success():
    report_id = uuid.uuid4()
    mock_data = {
        "id": str(report_id),
        "reported_id": str(_MOCK_USER_ID),
        "entity_type": "post",
        "entity_id": str(uuid.uuid4()),
        "reason": "Test reason",
        "status": "under_review",
        "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z",
    }
    mock_response = ReportResponse(status=True, message="Success", data=mock_data)

    with patch(
        "apps.report.routes.get_report_details_admin_service",
        AsyncMock(return_value=mock_response),
    ) as detail_svc:
        response = client.get(f"/api/v1/admin/reports/{report_id}")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] is True
    detail_svc.assert_awaited_once()


def test_review_report_admin_success():
    report_id = uuid.uuid4()
    payload = {
        "report_id": str(report_id),
        "status": "actioned",
        "admin_comment": "Post removed",
    }

    mock_data = {
        "id": str(report_id),
        "reported_id": str(_MOCK_USER_ID),
        "entity_type": "post",
        "entity_id": str(uuid.uuid4()),
        "reason": "Test reason",
        "status": "actioned",
        "moderator_id": str(_MOCK_ADMIN_ID),
        "admin_comment": "Post removed",
        "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z",
    }
    mock_response = ReportResponse(status=True, message="Success", data=mock_data)

    with patch("apps.report.routes.review_report_admin_service", AsyncMock(return_value=mock_response)) as review_svc:
        response = client.patch("/api/v1/admin/reports", json=payload)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] is True
    review_svc.assert_awaited_once()


def test_admin_routes_unauthorized():
    from common.exceptions import ApiError

    async def _mock_unauthorized_moderator():
        raise ApiError("Insufficient permissions")

    app.dependency_overrides[get_current_moderator] = _mock_unauthorized_moderator
    try:
        response = client.get(
            "/api/v1/admin/reports",
            params={
                "entity_type": "post",
                "entity_id": str(_MOCK_ENTITY_ID),
            },
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Insufficient permissions" in response.json()["message"]
    finally:
        app.dependency_overrides[get_current_moderator] = _mock_current_moderator
