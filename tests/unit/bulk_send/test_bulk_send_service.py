from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile

from apps.accounts.db_models import User
from apps.bulk_send.enums import BulkEmailTargetType, EmailCampaignStatus
from apps.bulk_send.schemas import (
    AttachmentMeta,
    BulkEmailTarget,
    CreateBulkCampaignRequest,
    DeliveryStats,
)
from apps.bulk_send.recipient_resolution import BulkEmailAudienceResult
from apps.bulk_send.service import BulkSendService
from apps.bulk_send.storage import upload_attachment_file
from common.exceptions import ApiError


def _patch_recipient_resolution(
    monkeypatch,
    users: list[User],
    *,
    preference_excluded: list[User] | None = None,
):
    """Stub DB-backed audience resolution for create_campaign unit tests."""

    excluded = preference_excluded or []

    async def _resolve(db, *, targets, is_alumni=False):
        return BulkEmailAudienceResult(
            eligible_user_ids=[user.id for user in users],
            preference_excluded_user_ids=[user.id for user in excluded],
        )

    async def _load(db, user_ids):
        by_id = {user.id: user for user in [*users, *excluded]}
        return [by_id[uid] for uid in user_ids if uid in by_id]

    monkeypatch.setattr(
        "apps.bulk_send.service.resolve_bulk_email_audience",
        _resolve,
    )
    monkeypatch.setattr(
        "apps.bulk_send.service.load_users_by_ids_preserving_order",
        _load,
    )


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def build_tmp_key(self, admin_id, filename: str) -> str:
        return f"email-campaigns/tmp/{admin_id}/{uuid4()}/{filename}"

    def upload(self, storage_key: str, data: bytes, content_type: str) -> str:
        self.objects[storage_key] = data
        return storage_key

    def exists(self, storage_key: str) -> bool:
        return storage_key in self.objects

    def download(self, storage_key: str) -> bytes:
        return self.objects[storage_key]


class _Result:
    def __init__(self, values=None, value=None):
        self._values = values or []
        self._value = value

    def scalars(self):
        return self

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None

    def scalar_one(self):
        return self._value

    def one(self):
        return self._value


class _Session:
    def __init__(self, users: list[User] | None = None):
        self.users = users or []
        self.added: list = []
        self.committed = False

    async def execute(self, stmt):
        # Recipient lookup
        return _Result(values=list(self.users))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()


@pytest.mark.asyncio
async def test_create_campaign_with_multiple_recipients(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    u1 = User(id=uuid4(), email="a@example.com")
    u2 = User(id=uuid4(), email="b@example.com")
    session = _Session(users=[u1, u2])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    _patch_recipient_resolution(monkeypatch, [u1, u2])

    result = await service.create_campaign(
        admin=admin,
        payload=CreateBulkCampaignRequest(
            name="August Announcement",
            subject="Update",
            body_html="<p>Hello</p>",
            body_text="Hello",
            user_ids=[u1.id, u2.id],
        ),
        db=session,  # type: ignore[arg-type]
    )

    assert result.status == EmailCampaignStatus.queued
    assert result.total_recipients == 2
    assert session.committed is True
    # campaign + 2 deliveries
    assert len(session.added) == 3
    assert session.added[0].attachments == []


@pytest.mark.asyncio
async def test_list_campaigns_page_includes_body_text(monkeypatch):
    now = datetime.now(timezone.utc)
    campaign_id = uuid4()

    class _Campaign:
        id = campaign_id
        name = "August"
        subject = "Update"
        body_text = "Hello from campaign"
        status = EmailCampaignStatus.queued
        created_by = uuid4()
        total_recipients = 2
        started_at = None
        completed_at = None
        created_at = now
        updated_at = now

    async def _list(_db, page=1, page_size=20, search=None):
        return [_Campaign()], 1

    monkeypatch.setattr("apps.bulk_send.service.list_campaigns", _list)
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    data = await service.list_campaigns_page(db=object(), page=1, page_size=20)  # type: ignore[arg-type]
    assert data["items"][0]["body_text"] == "Hello from campaign"
    assert data["items"][0]["id"] == str(campaign_id)


@pytest.mark.asyncio
async def test_list_campaigns_page_forwards_search(monkeypatch):
    captured = {}

    async def _list(_db, page=1, page_size=20, search=None):
        captured["page"] = page
        captured["page_size"] = page_size
        captured["search"] = search
        return [], 0

    monkeypatch.setattr("apps.bulk_send.service.list_campaigns", _list)
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    await service.list_campaigns_page(
        db=object(), page=1, page_size=10, search="welcome"  # type: ignore[arg-type]
    )
    assert captured == {"page": 1, "page_size": 10, "search": "welcome"}


def test_create_campaign_request_attachments_optional():
    user_id = uuid4()
    base = {
        "name": "No files",
        "subject": "Hello",
        "body_html": "<p>Hi</p>",
        "user_ids": [str(user_id)],
    }
    omitted = CreateBulkCampaignRequest.model_validate(base)
    assert omitted.attachments == []

    null_attachments = CreateBulkCampaignRequest.model_validate({**base, "attachments": None})
    assert null_attachments.attachments == []

    empty = CreateBulkCampaignRequest.model_validate({**base, "attachments": []})
    assert empty.attachments == []


@pytest.mark.asyncio
async def test_create_campaign_rejects_invalid_recipients(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    session = _Session(users=[])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    missing = uuid4()
    _patch_recipient_resolution(monkeypatch, [])

    with pytest.raises(ApiError, match="No eligible recipients found"):
        await service.create_campaign(
            admin=admin,
            payload=CreateBulkCampaignRequest(
                name="Bad",
                subject="Bad",
                body_html="<p>x</p>",
                user_ids=[missing],
            ),
            db=session,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_create_campaign_single_recipient_bulk_email_opted_out(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    opted_out = User(id=uuid4(), email="opted.out@example.com")
    session = _Session(users=[])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    _patch_recipient_resolution(monkeypatch, [], preference_excluded=[opted_out])

    with pytest.raises(
        ApiError,
        match=(
            "Bulk email cannot be sent: opted.out@example.com has turned off "
            "bulk email notifications"
        ),
    ):
        await service.create_campaign(
            admin=admin,
            payload=CreateBulkCampaignRequest(
                name="Opt out",
                subject="Hello",
                body_html="<p>x</p>",
                user_ids=[opted_out.id],
            ),
            db=session,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_create_campaign_all_recipients_bulk_email_opted_out(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    opted_out_a = User(id=uuid4(), email="a@example.com")
    opted_out_b = User(id=uuid4(), email="b@example.com")
    session = _Session(users=[])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    _patch_recipient_resolution(
        monkeypatch,
        [],
        preference_excluded=[opted_out_a, opted_out_b],
    )

    with pytest.raises(
        ApiError,
        match="All selected recipients have turned off bulk email notifications",
    ):
        await service.create_campaign(
            admin=admin,
            payload=CreateBulkCampaignRequest(
                name="Opt outs",
                subject="Hello",
                body_html="<p>x</p>",
                user_ids=[opted_out_a.id, opted_out_b.id],
            ),
            db=session,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_create_campaign_with_attachment_metadata(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    recipient = User(id=uuid4(), email="r@example.com")
    storage = FakeStorage()
    key = f"email-campaigns/tmp/{admin.id}/{uuid4()}/note.pdf"
    storage.objects[key] = b"%PDF-1.4"
    service = BulkSendService(storage=storage)  # type: ignore[arg-type]
    session = _Session(users=[recipient])
    _patch_recipient_resolution(monkeypatch, [recipient])

    result = await service.create_campaign(
        admin=admin,
        payload=CreateBulkCampaignRequest(
            name="With attachment",
            subject="File",
            body_html="<p>see attached</p>",
            user_ids=[recipient.id],
            attachments=[
                AttachmentMeta(
                    file_name="note.pdf",
                    storage_key=key,
                    content_type="application/pdf",
                )
            ],
        ),
        db=session,  # type: ignore[arg-type]
    )
    campaign = session.added[0]
    assert result.total_recipients == 1
    assert campaign.attachments[0]["storage_key"] == key


@pytest.mark.asyncio
async def test_create_campaign_rejects_foreign_attachment_key(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    recipient = User(id=uuid4(), email="r@example.com")
    storage = FakeStorage()
    other_admin = uuid4()
    key = f"email-campaigns/tmp/{other_admin}/{uuid4()}/note.pdf"
    storage.objects[key] = b"x"
    service = BulkSendService(storage=storage)  # type: ignore[arg-type]
    _patch_recipient_resolution(monkeypatch, [recipient])

    with pytest.raises(ApiError, match="current admin"):
        await service.create_campaign(
            admin=admin,
            payload=CreateBulkCampaignRequest(
                name="Bad key",
                subject="File",
                body_html="<p>x</p>",
                user_ids=[recipient.id],
                attachments=[
                    AttachmentMeta(
                        file_name="note.pdf",
                        storage_key=key,
                        content_type="application/pdf",
                    )
                ],
            ),
            db=_Session(users=[recipient]),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_upload_attachment_helper():
    storage = FakeStorage()
    service = BulkSendService(storage=storage)  # type: ignore[arg-type]
    admin = User(id=uuid4(), email="admin@example.com")
    upload = UploadFile(
        filename="announce.pdf",
        file=BytesIO(b"%PDF-fake"),
        headers={"content-type": "application/pdf"},
    )

    data = await service.upload_attachment(admin=admin, file=upload)
    assert data.file_name == "announce.pdf"
    assert data.content_type == "application/pdf"
    assert data.storage_key in storage.objects
    assert data.storage_key.startswith(f"email-campaigns/tmp/{admin.id}/")


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type():
    storage = FakeStorage()
    admin = User(id=uuid4(), email="admin@example.com")
    upload = UploadFile(
        filename="x.exe",
        file=BytesIO(b"MZ"),
        headers={"content-type": "application/x-msdownload"},
    )

    with pytest.raises(HTTPException):
        await upload_attachment_file(file=upload, admin_id=admin.id, storage=storage)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_campaign_detail_stats(monkeypatch):
    campaign_id = uuid4()
    now = datetime.now(timezone.utc)

    class _Campaign:
        id = campaign_id
        name = "Stats"
        subject = "Stats"
        status = EmailCampaignStatus.queued
        created_by = uuid4()
        total_recipients = 2
        started_at = None
        completed_at = None
        created_at = now
        updated_at = now
        body_html = "<p>s</p>"
        body_text = None
        attachments = []

    async def _get(_db, _id):
        return _Campaign()

    async def _stats(_db, _id):
        return DeliveryStats(total=2, pending=2, processing=0, sent=0, failed=0)

    async def _deliveries(_db, _id, search=None, page=None, page_size=None):
        return [], 0

    monkeypatch.setattr("apps.bulk_send.service.get_campaign", _get)
    monkeypatch.setattr("apps.bulk_send.service.delivery_stats_for_campaign", _stats)
    monkeypatch.setattr("apps.bulk_send.service.get_campaign_deliveries_with_profiles", _deliveries)

    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    detail = await service.get_campaign_detail(db=object(), campaign_id=campaign_id)  # type: ignore[arg-type]
    assert detail.delivery_stats.total == 2
    assert detail.delivery_stats.pending == 2
    assert detail.recipients.items == []
    assert detail.recipients.totalItems == 0


@pytest.mark.asyncio
async def test_get_campaign_detail_with_recipients(monkeypatch):
    from types import SimpleNamespace
    from apps.bulk_send.enums import EmailDeliveryStatus

    campaign_id = uuid4()
    user_id = uuid4()
    now = datetime.now(timezone.utc)

    class _Campaign:
        id = campaign_id
        name = "Announcement"
        subject = "Hello"
        status = EmailCampaignStatus.completed
        created_by = uuid4()
        total_recipients = 1
        started_at = now
        completed_at = now
        created_at = now
        updated_at = now
        body_html = "<p>body</p>"
        body_text = "body"
        attachments = []

    delivery = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        email="john.doe@example.com",
        status=EmailDeliveryStatus.sent,
        delivered_at=now,
        last_attempt_at=now,
        failure_reason=None,
    )
    profile = SimpleNamespace(
        first_name="John",
        last_name="Doe",
    )

    async def _get(_db, _id):
        return _Campaign()

    async def _stats(_db, _id):
        return DeliveryStats(total=1, pending=0, processing=0, sent=1, failed=0)

    async def _deliveries(_db, _id, search=None, page=None, page_size=None):
        return [(delivery, profile)], 1

    monkeypatch.setattr("apps.bulk_send.service.get_campaign", _get)
    monkeypatch.setattr("apps.bulk_send.service.delivery_stats_for_campaign", _stats)
    monkeypatch.setattr("apps.bulk_send.service.get_campaign_deliveries_with_profiles", _deliveries)

    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    detail = await service.get_campaign_detail(db=object(), campaign_id=campaign_id)  # type: ignore[arg-type]

    assert len(detail.recipients.items) == 1
    recipient = detail.recipients.items[0]
    assert recipient.user_id == user_id
    assert recipient.first_name == "John"
    assert recipient.last_name == "Doe"
    assert recipient.user_name == "John Doe"
    assert recipient.email == "john.doe@example.com"
    assert recipient.status == EmailDeliveryStatus.sent
    assert recipient.is_delivered is True
    assert recipient.delivered_at == now
    assert detail.recipients.totalItems == 1
    assert detail.recipients.page == 1


@pytest.mark.asyncio
async def test_get_campaign_detail_paginates_recipients(monkeypatch):
    from types import SimpleNamespace
    from apps.bulk_send.enums import EmailDeliveryStatus

    campaign_id = uuid4()
    now = datetime.now(timezone.utc)
    captured = {}

    class _Campaign:
        id = campaign_id
        name = "Announcement"
        subject = "Hello"
        status = EmailCampaignStatus.completed
        created_by = uuid4()
        total_recipients = 3
        started_at = now
        completed_at = now
        created_at = now
        updated_at = now
        body_html = "<p>body</p>"
        body_text = "body"
        attachments = []

    deliveries = [
        (
            SimpleNamespace(
                id=uuid4(),
                user_id=uuid4(),
                email=f"user{index}@example.com",
                status=EmailDeliveryStatus.pending,
                delivered_at=None,
                last_attempt_at=None,
                failure_reason=None,
            ),
            SimpleNamespace(first_name=f"First{index}", last_name=f"Last{index}"),
        )
        for index in range(2)
    ]

    async def _get(_db, _id):
        return _Campaign()

    async def _stats(_db, _id):
        return DeliveryStats(total=3, pending=3, processing=0, sent=0, failed=0)

    async def _deliveries(_db, _id, search=None, page=None, page_size=None):
        captured["search"] = search
        captured["page"] = page
        captured["page_size"] = page_size
        return deliveries, 3

    monkeypatch.setattr("apps.bulk_send.service.get_campaign", _get)
    monkeypatch.setattr("apps.bulk_send.service.delivery_stats_for_campaign", _stats)
    monkeypatch.setattr("apps.bulk_send.service.get_campaign_deliveries_with_profiles", _deliveries)

    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    detail = await service.get_campaign_detail(
        db=object(),  # type: ignore[arg-type]
        campaign_id=campaign_id,
        page=1,
        page_size=2,
        search="First",
    )
    assert captured == {"search": "First", "page": 1, "page_size": 2}
    assert len(detail.recipients.items) == 2
    assert detail.recipients.page == 1
    assert detail.recipients.pageSize == 2
    assert detail.recipients.totalItems == 3
    assert detail.recipients.totalPages == 2


def test_recipient_name_search_clause_matches_first_and_last():
    from apps.bulk_send.repository import _recipient_name_search_clause

    assert _recipient_name_search_clause(None) is None
    assert _recipient_name_search_clause("   ") is None
    clause = _recipient_name_search_clause("Jane")
    assert clause is not None
    compiled = str(clause.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "first_name" in compiled
    assert "last_name" in compiled
    assert "%jane%" in compiled



@pytest.mark.asyncio
async def test_create_campaign_to_all(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    u1 = User(id=uuid4(), email="u1@example.com")
    u2 = User(id=uuid4(), email="u2@example.com")
    u3 = User(id=uuid4(), email="u3@example.com")
    session = _Session(users=[u1, u2, u3])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    _patch_recipient_resolution(monkeypatch, [u1, u2, u3])

    result = await service.create_campaign(
        admin=admin,
        payload=CreateBulkCampaignRequest(
            name="Broadcast To All",
            subject="All Hands",
            body_html="<p>Broadcast content</p>",
            to_all=True,
        ),
        db=session,  # type: ignore[arg-type]
    )

    assert result.status == EmailCampaignStatus.queued
    assert result.total_recipients == 3
    assert session.committed is True
    # 1 campaign + 3 deliveries
    assert len(session.added) == 4


@pytest.mark.asyncio
async def test_create_campaign_to_all_no_recipients_raises(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    session = _Session(users=[])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    _patch_recipient_resolution(monkeypatch, [])

    with pytest.raises(ApiError) as exc:
        await service.create_campaign(
            admin=admin,
            payload=CreateBulkCampaignRequest(
                name="Empty Broadcast",
                subject="Nobody",
                body_html="<p>Hello</p>",
                to_all=True,
            ),
            db=session,  # type: ignore[arg-type]
        )
    assert "No eligible recipients found" in str(exc.value)


def test_create_campaign_request_empty_targets_means_unrestricted():
    payload = CreateBulkCampaignRequest(
        name="Unrestricted",
        subject="Hello",
        body_html="<p>Hello</p>",
        targets=[],
    )
    assert payload.targets == []


def test_create_campaign_request_legacy_user_ids_normalize_to_targets():
    user_id = uuid4()
    payload = CreateBulkCampaignRequest(
        name="Legacy",
        subject="Hello",
        body_html="<p>Hi</p>",
        user_ids=[user_id],
    )
    assert payload.targets[0].type == BulkEmailTargetType.USER
    assert payload.targets[0].values == [str(user_id)]


@pytest.mark.asyncio
async def test_create_campaign_with_country_ids_only(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    country_id = uuid4()
    u1 = User(id=uuid4(), email="ca@example.com")
    session = _Session(users=[u1])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    _patch_recipient_resolution(monkeypatch, [u1])

    async def _validate_countries(_db, country_ids):
        assert country_ids == [country_id]

    monkeypatch.setattr(service, "_validate_countries", _validate_countries)

    result = await service.create_campaign(
        admin=admin,
        payload=CreateBulkCampaignRequest(
            name="Canada Update",
            subject="Hello Canada",
            body_html="<p>Hi</p>",
            country_ids=[country_id],
        ),
        db=session,  # type: ignore[arg-type]
    )

    assert result.total_recipients == 1
    assert session.committed is True


@pytest.mark.asyncio
async def test_create_campaign_to_all_with_country_ids(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    country_id = uuid4()
    u1 = User(id=uuid4(), email="us@example.com")
    session = _Session(users=[u1])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    _patch_recipient_resolution(monkeypatch, [u1])

    async def _validate_countries(_db, country_ids):
        assert country_ids == [country_id]

    monkeypatch.setattr(service, "_validate_countries", _validate_countries)

    result = await service.create_campaign(
        admin=admin,
        payload=CreateBulkCampaignRequest(
            name="Country Broadcast",
            subject="Country-wide",
            body_html="<p>Hi</p>",
            to_all=True,
            country_ids=[country_id],
        ),
        db=session,  # type: ignore[arg-type]
    )

    assert result.total_recipients == 1


@pytest.mark.asyncio
async def test_create_campaign_rejects_invalid_country_ids(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    country_id = uuid4()
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]

    async def _validate_countries(_db, _country_ids):
        raise ApiError("Invalid or inactive country_ids: bad")

    monkeypatch.setattr(service, "_validate_countries", _validate_countries)
    _patch_recipient_resolution(monkeypatch, [])

    with pytest.raises(ApiError, match="Invalid or inactive country_ids"):
        await service.create_campaign(
            admin=admin,
            payload=CreateBulkCampaignRequest(
                name="Bad Country",
                subject="Nope",
                body_html="<p>x</p>",
                country_ids=[country_id],
            ),
            db=_Session(),  # type: ignore[arg-type]
        )


def test_create_campaign_request_country_ids_without_user_ids_allowed():
    country_id = uuid4()
    payload = CreateBulkCampaignRequest(
        name="Country Campaign",
        subject="Hello",
        body_html="<p>Hi</p>",
        country_ids=[country_id],
    )
    assert payload.country_ids == [country_id]
    assert payload.user_ids == []


def test_create_campaign_request_is_alumni_true_without_user_ids_allowed():
    payload = CreateBulkCampaignRequest(
        name="Alumni Campaign",
        subject="Hello alumni",
        body_html="<p>Hi</p>",
        is_alumni=True,
    )
    assert payload.is_alumni is True
    assert payload.to_all is False
    assert payload.user_ids == []


def test_eligible_users_stmt_filters_alumni_only_when_requested():
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    without = str(
        service._eligible_users_stmt().compile(compile_kwargs={"literal_binds": True})
    ).lower()
    with_alumni = str(
        service._eligible_users_stmt(is_alumni=True).compile(
            compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert "is_alumni" not in without
    assert "is_alumni" in with_alumni
    assert "true" in with_alumni


@pytest.mark.asyncio
async def test_create_campaign_is_alumni_true_sends_to_alumni(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    alumni = User(id=uuid4(), email="alumni@example.com")
    session = _Session(users=[alumni])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    _patch_recipient_resolution(monkeypatch, [alumni])

    result = await service.create_campaign(
        admin=admin,
        payload=CreateBulkCampaignRequest(
            name="Alumni Broadcast",
            subject="Hello alumni",
            body_html="<p>Congrats</p>",
            is_alumni=True,
        ),
        db=session,  # type: ignore[arg-type]
    )

    assert result.status == EmailCampaignStatus.queued
    assert result.total_recipients == 1
    assert session.committed is True
    assert len(session.added) == 2


@pytest.mark.asyncio
async def test_create_campaign_is_alumni_false_keeps_explicit_recipients(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    u1 = User(id=uuid4(), email="a@example.com")
    session = _Session(users=[u1])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    _patch_recipient_resolution(monkeypatch, [u1])

    result = await service.create_campaign(
        admin=admin,
        payload=CreateBulkCampaignRequest(
            name="Targeted",
            subject="Hello",
            body_html="<p>Hi</p>",
            is_alumni=False,
            user_ids=[u1.id],
        ),
        db=session,  # type: ignore[arg-type]
    )

    assert result.total_recipients == 1


@pytest.mark.asyncio
async def test_create_campaign_with_targets_passes_to_resolver(monkeypatch):
    admin = User(id=uuid4(), email="admin@example.com")
    u1 = User(id=uuid4(), email="a@example.com")
    session = _Session(users=[u1])
    service = BulkSendService(storage=FakeStorage())  # type: ignore[arg-type]
    captured: dict = {}

    async def _resolve(db, *, targets, is_alumni=False):
        captured["targets"] = targets
        captured["is_alumni"] = is_alumni
        return BulkEmailAudienceResult(
            eligible_user_ids=[u1.id],
            preference_excluded_user_ids=[],
        )

    async def _load(db, user_ids):
        return [u1]

    monkeypatch.setattr("apps.bulk_send.service.resolve_bulk_email_audience", _resolve)
    monkeypatch.setattr(
        "apps.bulk_send.service.load_users_by_ids_preserving_order",
        _load,
    )

    uni_id = str(uuid4())
    result = await service.create_campaign(
        admin=admin,
        payload=CreateBulkCampaignRequest(
            name="AI Campaign",
            subject="Update",
            body_html="<p>Hello</p>",
            is_alumni=True,
            targets=[
                BulkEmailTarget(
                    type=BulkEmailTargetType.MAJOR,
                    to_all=False,
                    values=["42"],
                ),
                BulkEmailTarget(
                    type=BulkEmailTargetType.UNIVERSITY,
                    to_all=False,
                    values=[uni_id],
                ),
            ],
        ),
        db=session,  # type: ignore[arg-type]
    )

    assert result.total_recipients == 1
    assert captured["is_alumni"] is True
    assert [t.type for t in captured["targets"]] == [
        BulkEmailTargetType.MAJOR,
        BulkEmailTargetType.UNIVERSITY,
    ]
    assert session.committed is True

