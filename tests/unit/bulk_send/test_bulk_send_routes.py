from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.bulk_send.enums import EmailCampaignStatus
from apps.bulk_send.schemas import (
    AttachmentUploadData,
    CampaignDetail,
    CreateCampaignData,
    DeliveryStats,
)
from apps.bulk_send.service import BulkSendService, get_bulk_send_service
from common.exceptions import ApiError
from common.pagination import PaginatedResponse
from core.database.session import get_session
from core.security.auth import get_current_admin
from entrypoints.api import app
from tests.unit.conftest import FakeScalarResult
from apps.administration.dependencies import require_signed_admin

client = TestClient(app)
ADMIN_ID = uuid4()
CAMPAIGN_ID = uuid4()


async def _override_admin():
    return User(id=ADMIN_ID, email="admin@example.com", firebase_uid="admin-uid")


class _Session:
    async def execute(self, *_args, **_kwargs):
        return FakeScalarResult()

    def add(self, obj):
        return None

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None

    async def flush(self):
        return None


def setup_module() -> None:
    app.dependency_overrides[get_current_admin] = _override_admin
    app.dependency_overrides[require_signed_admin] = _override_admin

    async def _override_session():
        yield _Session()

    app.dependency_overrides[get_session] = _override_session


def teardown_module() -> None:
    app.dependency_overrides.pop(get_current_admin, None)
    app.dependency_overrides.pop(require_signed_admin, None)
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_bulk_send_service, None)


def test_create_campaign_requires_admin() -> None:
    override = app.dependency_overrides.pop(get_current_admin, None)
    override = app.dependency_overrides.pop(require_signed_admin, None)
    try:
        response = client.post(
            "/api/v1/admin/bulk-send/campaigns",
            json={
                "name": "x",
                "subject": "y",
                "body_html": "<p>z</p>",
                "user_ids": [str(uuid4())],
            },
        )
        assert response.status_code in (200, 401)
        body = response.json()
        assert body["status"] is False
    finally:
        if override:
            app.dependency_overrides[get_current_admin] = override
            app.dependency_overrides[require_signed_admin] = override


def test_create_campaign_success(monkeypatch) -> None:
    data = CreateCampaignData(
        campaign_id=CAMPAIGN_ID,
        status=EmailCampaignStatus.queued,
        total_recipients=2,
    )

    async def _create_campaign(*, admin, payload, db):
        assert admin.id == ADMIN_ID
        assert len(payload.user_ids) == 2
        assert payload.attachments == []
        return data

    service = BulkSendService()
    monkeypatch.setattr(service, "create_campaign", _create_campaign)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.post(
        "/api/v1/admin/bulk-send/campaigns",
        json={
            "name": "August Announcement",
            "subject": "Important update",
            "body_html": "<h1>Hello</h1>",
            "body_text": "Hello",
            "user_ids": [str(uuid4()), str(uuid4())],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["campaign_id"] == str(CAMPAIGN_ID)
    assert body["data"]["status"] == "queued"


def test_create_campaign_accepts_null_attachments(monkeypatch) -> None:
    data = CreateCampaignData(
        campaign_id=CAMPAIGN_ID,
        status=EmailCampaignStatus.queued,
        total_recipients=1,
    )

    async def _create_campaign(*, admin, payload, db):
        assert payload.attachments == []
        return data

    service = BulkSendService()
    monkeypatch.setattr(service, "create_campaign", _create_campaign)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.post(
        "/api/v1/admin/bulk-send/campaigns",
        json={
            "name": "No attachments",
            "subject": "Update",
            "body_html": "<p>Hello</p>",
            "user_ids": [str(uuid4())],
            "attachments": None,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is True


def test_create_campaign_invalid_recipients(monkeypatch) -> None:
    async def _create_campaign(*, admin, payload, db):
        raise ApiError("Invalid recipient user_ids: deadbeef")

    service = BulkSendService()
    monkeypatch.setattr(service, "create_campaign", _create_campaign)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.post(
        "/api/v1/admin/bulk-send/campaigns",
        json={
            "name": "Bad",
            "subject": "Bad",
            "body_html": "<p>x</p>",
            "user_ids": [str(uuid4())],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is False
    assert "Invalid recipient" in body["message"]


def test_get_campaign_detail(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    detail = CampaignDetail(
        id=CAMPAIGN_ID,
        name="August",
        subject="Update",
        status=EmailCampaignStatus.queued,
        created_by=ADMIN_ID,
        total_recipients=2,
        started_at=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
        body_html="<p>Hello</p>",
        body_text="Hello",
        attachments=[],
        delivery_stats=DeliveryStats(total=2, pending=2, processing=0, sent=0, failed=0),
        recipients=PaginatedResponse(
            items=[],
            page=1,
            pageSize=1,
            totalItems=0,
            totalPages=0,
        ),
    )

    async def _get_detail(*, db, campaign_id, page=None, page_size=None, search=None):
        assert campaign_id == CAMPAIGN_ID
        return detail

    service = BulkSendService()
    monkeypatch.setattr(service, "get_campaign_detail", _get_detail)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.get(f"/api/v1/admin/bulk-send/campaigns/{CAMPAIGN_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["delivery_stats"]["pending"] == 2
    assert body["data"]["recipients"]["items"] == []
    assert body["data"]["recipients"]["totalItems"] == 0


def test_get_campaign_detail_passes_pagination_and_search(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    captured = {}
    detail = CampaignDetail(
        id=CAMPAIGN_ID,
        name="August",
        subject="Update",
        status=EmailCampaignStatus.queued,
        created_by=ADMIN_ID,
        total_recipients=2,
        started_at=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
        body_html="<p>Hello</p>",
        body_text="Hello",
        attachments=[],
        delivery_stats=DeliveryStats(total=2, pending=2, processing=0, sent=0, failed=0),
        recipients=PaginatedResponse(
            items=[],
            page=2,
            pageSize=10,
            totalItems=0,
            totalPages=0,
        ),
    )

    async def _get_detail(*, db, campaign_id, page=None, page_size=None, search=None):
        captured["page"] = page
        captured["page_size"] = page_size
        captured["search"] = search
        return detail

    service = BulkSendService()
    monkeypatch.setattr(service, "get_campaign_detail", _get_detail)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.get(
        f"/api/v1/admin/bulk-send/campaigns/{CAMPAIGN_ID}",
        params={"page": 2, "pageSize": 10, "search": "jane"},
    )
    assert response.status_code == 200
    assert response.json()["status"] is True
    assert captured == {"page": 2, "page_size": 10, "search": "jane"}


def test_get_campaign_detail_omits_pagination_to_return_all(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    captured = {}
    detail = CampaignDetail(
        id=CAMPAIGN_ID,
        name="August",
        subject="Update",
        status=EmailCampaignStatus.queued,
        created_by=ADMIN_ID,
        total_recipients=2,
        started_at=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
        body_html="<p>Hello</p>",
        attachments=[],
        delivery_stats=DeliveryStats(total=2, pending=2, processing=0, sent=0, failed=0),
        recipients=PaginatedResponse(
            items=[],
            page=1,
            pageSize=1,
            totalItems=0,
            totalPages=0,
        ),
    )

    async def _get_detail(*, db, campaign_id, page=None, page_size=None, search=None):
        captured["page"] = page
        captured["page_size"] = page_size
        captured["search"] = search
        return detail

    service = BulkSendService()
    monkeypatch.setattr(service, "get_campaign_detail", _get_detail)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.get(f"/api/v1/admin/bulk-send/campaigns/{CAMPAIGN_ID}")
    assert response.status_code == 200
    assert captured == {"page": None, "page_size": None, "search": None}


def test_upload_attachment(monkeypatch) -> None:
    async def _upload(*, admin, file):
        return AttachmentUploadData(
            file_name="a.pdf",
            storage_key=f"email-campaigns/tmp/{admin.id}/x/a.pdf",
            content_type="application/pdf",
        )

    service = BulkSendService()
    monkeypatch.setattr(service, "upload_attachment", _upload)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.post(
        "/api/v1/admin/bulk-send/attachments",
        files={"file": ("a.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["file_name"] == "a.pdf"


def test_list_campaigns_passes_search(monkeypatch) -> None:
    captured = {}

    async def _list_page(*, db, page, page_size, search=None):
        captured["page"] = page
        captured["page_size"] = page_size
        captured["search"] = search
        return {
            "items": [],
            "page": page,
            "pageSize": page_size,
            "totalItems": 0,
            "totalPages": 0,
        }

    service = BulkSendService()
    monkeypatch.setattr(service, "list_campaigns_page", _list_page)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.get(
        "/api/v1/admin/bulk-send/campaigns",
        params={"page": 1, "pageSize": 10, "search": "welcome"},
    )
    assert response.status_code == 200
    assert response.json()["status"] is True
    assert captured == {"page": 1, "page_size": 10, "search": "welcome"}


def test_create_campaign_to_all_route_success(monkeypatch) -> None:
    data = CreateCampaignData(
        campaign_id=CAMPAIGN_ID,
        status=EmailCampaignStatus.queued,
        total_recipients=150,
    )

    async def _create_campaign(*, admin, payload, db):
        assert payload.to_all is True
        assert payload.user_ids == []
        return data

    service = BulkSendService()
    monkeypatch.setattr(service, "create_campaign", _create_campaign)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.post(
        "/api/v1/admin/bulk-send/campaigns",
        json={
            "name": "Global Announcement",
            "subject": "Important update for all",
            "body_html": "<h1>Hello Everyone</h1>",
            "to_all": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["campaign_id"] == str(CAMPAIGN_ID)
    assert body["data"]["total_recipients"] == 150


def test_create_campaign_empty_targets_route_success(monkeypatch) -> None:
    data = CreateCampaignData(
        campaign_id=CAMPAIGN_ID,
        status=EmailCampaignStatus.queued,
        total_recipients=10,
    )

    async def _create_campaign(*, admin, payload, db):
        assert payload.targets == []
        assert payload.to_all is False
        return data

    service = BulkSendService()
    monkeypatch.setattr(service, "create_campaign", _create_campaign)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.post(
        "/api/v1/admin/bulk-send/campaigns",
        json={
            "name": "Unrestricted Campaign",
            "subject": "Hello",
            "body_html": "<p>Content</p>",
            "targets": [],
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["status"] is True


def test_create_campaign_with_targets_route_success(monkeypatch) -> None:
    data = CreateCampaignData(
        campaign_id=CAMPAIGN_ID,
        status=EmailCampaignStatus.queued,
        total_recipients=5,
    )
    uni_id = str(uuid4())

    async def _create_campaign(*, admin, payload, db):
        assert len(payload.targets) == 4
        assert payload.targets[0].type.value == "MAJOR"
        assert payload.targets[1].type.value == "MINOR"
        assert payload.targets[2].type.value == "EDUCATION_LEVEL"
        assert payload.targets[3].type.value == "UNIVERSITY"
        assert payload.is_alumni is True
        return data

    service = BulkSendService()
    monkeypatch.setattr(service, "create_campaign", _create_campaign)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.post(
        "/api/v1/admin/bulk-send/campaigns",
        json={
            "name": "AI Campaign",
            "subject": "Update",
            "body_html": "<p>Hello</p>",
            "is_alumni": True,
            "targets": [
                {"type": "MAJOR", "to_all": False, "values": ["1", "2"]},
                {"type": "MINOR", "to_all": False, "values": ["10", "Data Science"]},
                {"type": "EDUCATION_LEVEL", "to_all": False, "values": ["Bachelors"]},
                {"type": "UNIVERSITY", "to_all": False, "values": [uni_id]},
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] is True


def test_create_campaign_invalid_target_to_all_with_values_fails() -> None:
    response = client.post(
        "/api/v1/admin/bulk-send/campaigns",
        json={
            "name": "Bad",
            "subject": "Bad",
            "body_html": "<p>x</p>",
            "targets": [
                {"type": "MAJOR", "to_all": True, "values": ["1"]},
            ],
        },
    )
    body = response.json()
    assert body["status"] is False
    assert "values must be empty when to_all is true" in body["message"]


def test_create_campaign_with_country_ids_route_success(monkeypatch) -> None:
    country_id = uuid4()
    data = CreateCampaignData(
        campaign_id=CAMPAIGN_ID,
        status=EmailCampaignStatus.queued,
        total_recipients=42,
    )

    async def _create_campaign(*, admin, payload, db):
        assert payload.country_ids == [country_id]
        assert payload.to_all is False
        assert payload.user_ids == []
        return data

    service = BulkSendService()
    monkeypatch.setattr(service, "create_campaign", _create_campaign)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.post(
        "/api/v1/admin/bulk-send/campaigns",
        json={
            "name": "Canada Announcement",
            "subject": "Hello Canada",
            "body_html": "<p>Hi</p>",
            "to_all": False,
            "country_ids": [str(country_id)],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["total_recipients"] == 42


def test_create_campaign_is_alumni_true_route_success(monkeypatch) -> None:
    data = CreateCampaignData(
        campaign_id=CAMPAIGN_ID,
        status=EmailCampaignStatus.queued,
        total_recipients=25,
    )

    async def _create_campaign(*, admin, payload, db):
        assert payload.is_alumni is True
        assert payload.to_all is False
        assert payload.user_ids == []
        return data

    service = BulkSendService()
    monkeypatch.setattr(service, "create_campaign", _create_campaign)
    app.dependency_overrides[get_bulk_send_service] = lambda: service

    response = client.post(
        "/api/v1/admin/bulk-send/campaigns",
        json={
            "name": "Alumni Announcement",
            "subject": "Hello alumni",
            "body_html": "<p>Congrats</p>",
            "is_alumni": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] is True
    assert body["data"]["total_recipients"] == 25

