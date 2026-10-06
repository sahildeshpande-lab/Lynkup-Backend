from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from apps.bulk_send.cron import BULK_EMAIL_CRON_INTERVAL_SECONDS, cron_send_bulk_emails
from apps.bulk_send.delivery_service import (
    MAX_DELIVERY_ATTEMPTS,
    _chunked,
    _finalize_batch,
    _finalize_delivery,
    _load_attachment_payloads,
    process_pending_bulk_deliveries,
)
from apps.bulk_send.enums import (
    SENDGRID_MAX_PERSONALIZATIONS,
    EmailCampaignStatus,
    EmailDeliveryStatus,
)
from apps.bulk_send.schemas import BulkBatchSendResult, DeliveryResult
from core.email_service import cron_send_emails, process_pending_bulk_emails, process_pending_emails


def _campaign(**overrides):
    base = {
        "id": uuid4(),
        "name": "Cron",
        "subject": "Hello",
        "body_html": "<p>Hello</p>",
        "body_text": "Hello",
        "status": EmailCampaignStatus.queued,
        "attachments": [],
        "started_at": None,
        "completed_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _delivery(**overrides):
    base = {
        "id": uuid4(),
        "campaign_id": uuid4(),
        "user_id": uuid4(),
        "email": "recv@example.com",
        "status": EmailDeliveryStatus.pending,
        "attempt_count": 1,
        "sendgrid_message_id": None,
        "delivered_at": None,
        "failure_reason": None,
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _SessionCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return False


def _patch_delivery_pipeline(monkeypatch, *, claim, campaign, fake_batch, finalize_batch_mock=None):
    async def _mark(session, campaign_id):
        campaign.status = EmailCampaignStatus.processing
        campaign.started_at = campaign.started_at or datetime.now(timezone.utc)

    async def _get(session, campaign_id):
        return campaign

    async def _default_finalize(*_a, **_k):
        return None

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.async_session_factory",
        lambda: _SessionCtx(),
    )
    monkeypatch.setattr("apps.bulk_send.delivery_service.claim_pending_deliveries", claim)
    monkeypatch.setattr("apps.bulk_send.delivery_service.mark_campaign_processing", _mark)
    monkeypatch.setattr("apps.bulk_send.delivery_service.get_campaign", _get)
    monkeypatch.setattr("apps.bulk_send.delivery_service.send_bulk_campaign_batch", fake_batch)
    monkeypatch.setattr(
        "apps.bulk_send.delivery_service._finalize_batch",
        finalize_batch_mock or _default_finalize,
    )

    async def _all_eligible(user_ids):
        return list(user_ids)

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service._eligible_bulk_email_user_ids",
        _all_eligible,
    )

    async def _render_bulk(**kwargs):
        body = kwargs.get("body_html") or ""
        subject = kwargs.get("subject") or ""
        return (
            "<!doctype html><html><body>"
            '<div class="email-container">'
            f"<div>{subject}</div>"
            f"{body}"
            "<div>From your friends at KampuLynk 😊</div>"
            "</div></body></html>"
        )

    monkeypatch.setattr(
        "apps.bulk_send.email_template.render_bulk_campaign_html",
        _render_bulk,
    )


@pytest.mark.asyncio
async def test_process_pending_bulk_batches_by_campaign(monkeypatch):
    campaign = _campaign()
    d1 = _delivery(campaign_id=campaign.id, attempt_count=1, email="a@example.com")
    d2 = _delivery(campaign_id=campaign.id, attempt_count=1, email="b@example.com")
    batches: list[list[dict]] = []
    finalized: list[tuple] = []

    async def _claim(session, limit=SENDGRID_MAX_PERSONALIZATIONS):
        return [d1, d2]

    async def _fake_batch(**kwargs):
        batches.append(list(kwargs["recipients"]))
        assert kwargs["subject"] == f"{campaign.name} | {campaign.subject}"
        assert campaign.body_html in kwargs["html_body"]
        assert "<strong>Name:</strong>" not in kwargs["html_body"]
        assert "<strong>Subject:</strong>" not in kwargs["html_body"]
        assert "email-container" in kwargs["html_body"]
        assert campaign.subject in kwargs["html_body"]
        assert "From your friends at KampuLynk" in kwargs["html_body"]
        assert "automated security notification" not in kwargs["html_body"]
        assert kwargs["plain_text"] == campaign.body_text
        return BulkBatchSendResult(
            success=True,
            sendgrid_message_id="msg-batch-1",
            retryable=False,
            recipient_count=len(kwargs["recipients"]),
        )

    async def _finalize_batch_mock(deliveries, campaign_id, result, *, session_factory=None):
        for delivery in deliveries:
            finalized.append((delivery.id, result.success, result.sendgrid_message_id))

    _patch_delivery_pipeline(
        monkeypatch,
        claim=_claim,
        campaign=campaign,
        fake_batch=_fake_batch,
        finalize_batch_mock=_finalize_batch_mock,
    )

    processed = await process_pending_bulk_deliveries(limit=1000)
    assert processed == 2
    assert len(batches) == 1
    assert len(batches[0]) == 2
    assert {r["email"] for r in batches[0]} == {"a@example.com", "b@example.com"}
    for recipient in batches[0]:
        assert recipient["campaign_id"] == campaign.id
        assert "delivery_id" in recipient
    assert len(finalized) == 2
    assert all(item[2] == "msg-batch-1" for item in finalized)
    assert campaign.status == EmailCampaignStatus.processing


@pytest.mark.asyncio
async def test_exactly_one_sendgrid_call_for_1000_recipients(monkeypatch):
    campaign = _campaign()
    deliveries = [
        _delivery(campaign_id=campaign.id, email=f"u{i}@example.com", attempt_count=1)
        for i in range(1000)
    ]
    batch_sizes: list[int] = []

    async def _claim(session, limit=SENDGRID_MAX_PERSONALIZATIONS):
        return deliveries

    async def _fake_batch(**kwargs):
        batch_sizes.append(len(kwargs["recipients"]))
        return BulkBatchSendResult(
            success=True,
            sendgrid_message_id="msg",
            recipient_count=len(kwargs["recipients"]),
        )

    _patch_delivery_pipeline(
        monkeypatch, claim=_claim, campaign=campaign, fake_batch=_fake_batch
    )

    processed = await process_pending_bulk_deliveries(limit=1000)
    assert processed == 1000
    assert batch_sizes == [1000]


@pytest.mark.asyncio
async def test_chunking_splits_over_1000(monkeypatch):
    campaign = _campaign()
    deliveries = [
        _delivery(campaign_id=campaign.id, email=f"u{i}@example.com", attempt_count=1)
        for i in range(1001)
    ]
    batch_sizes: list[int] = []

    async def _claim(session, limit=SENDGRID_MAX_PERSONALIZATIONS):
        return deliveries

    async def _fake_batch(**kwargs):
        batch_sizes.append(len(kwargs["recipients"]))
        return BulkBatchSendResult(
            success=True,
            sendgrid_message_id="msg",
            recipient_count=len(kwargs["recipients"]),
        )

    _patch_delivery_pipeline(
        monkeypatch, claim=_claim, campaign=campaign, fake_batch=_fake_batch
    )

    processed = await process_pending_bulk_deliveries(limit=1001)
    assert processed == 1001
    assert batch_sizes == [1000, 1]


@pytest.mark.asyncio
async def test_chunking_2500_recipients_three_sendgrid_calls(monkeypatch):
    campaign = _campaign()
    deliveries = [
        _delivery(campaign_id=campaign.id, email=f"u{i}@example.com", attempt_count=1)
        for i in range(2500)
    ]
    batch_sizes: list[int] = []

    async def _claim(session, limit=SENDGRID_MAX_PERSONALIZATIONS):
        return deliveries

    async def _fake_batch(**kwargs):
        batch_sizes.append(len(kwargs["recipients"]))
        return BulkBatchSendResult(
            success=True,
            sendgrid_message_id="msg",
            recipient_count=len(kwargs["recipients"]),
        )

    _patch_delivery_pipeline(
        monkeypatch, claim=_claim, campaign=campaign, fake_batch=_fake_batch
    )

    processed = await process_pending_bulk_deliveries(limit=2500)
    assert processed == 2500
    assert batch_sizes == [1000, 1000, 500]


@pytest.mark.asyncio
async def test_multiple_campaigns_never_combined(monkeypatch):
    campaign_a = _campaign(subject="A", body_html="<p>A</p>", body_text="A")
    campaign_b = _campaign(subject="B", body_html="<p>B</p>", body_text="B")
    deliveries = [
        _delivery(campaign_id=campaign_a.id, email="a1@example.com", attempt_count=1),
        _delivery(campaign_id=campaign_a.id, email="a2@example.com", attempt_count=1),
        _delivery(campaign_id=campaign_b.id, email="b1@example.com", attempt_count=1),
    ]
    campaigns = {campaign_a.id: campaign_a, campaign_b.id: campaign_b}
    send_calls: list[dict] = []

    async def _claim(session, limit=SENDGRID_MAX_PERSONALIZATIONS):
        return deliveries

    async def _mark(session, campaign_id):
        campaigns[campaign_id].status = EmailCampaignStatus.processing

    async def _get(session, campaign_id):
        return campaigns[campaign_id]

    async def _fake_batch(**kwargs):
        send_calls.append(kwargs)
        return BulkBatchSendResult(
            success=True,
            sendgrid_message_id="msg",
            recipient_count=len(kwargs["recipients"]),
        )

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.async_session_factory",
        lambda: _SessionCtx(),
    )
    monkeypatch.setattr("apps.bulk_send.delivery_service.claim_pending_deliveries", _claim)
    monkeypatch.setattr("apps.bulk_send.delivery_service.mark_campaign_processing", _mark)
    monkeypatch.setattr("apps.bulk_send.delivery_service.get_campaign", _get)
    monkeypatch.setattr("apps.bulk_send.delivery_service.send_bulk_campaign_batch", _fake_batch)

    async def _finalize(*_a, **_k):
        return None

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service._finalize_batch",
        _finalize,
    )

    async def _all_eligible(user_ids):
        return list(user_ids)

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service._eligible_bulk_email_user_ids",
        _all_eligible,
    )

    async def _render_bulk(**kwargs):
        return f"<html>{kwargs.get('body_html') or ''}</html>"

    monkeypatch.setattr(
        "apps.bulk_send.email_template.render_bulk_campaign_html",
        _render_bulk,
    )

    processed = await process_pending_bulk_deliveries(limit=1000)
    assert processed == 3
    assert len(send_calls) == 2
    campaign_ids_per_call = [
        {r["campaign_id"] for r in call["recipients"]} for call in send_calls
    ]
    assert all(len(ids) == 1 for ids in campaign_ids_per_call)
    assert {next(iter(ids)) for ids in campaign_ids_per_call} == {
        campaign_a.id,
        campaign_b.id,
    }


def test_chunked_helper():
    assert _chunked(list(range(5)), 2) == [[0, 1], [2, 3], [4]]
    assert _chunked([], 1000) == []
    assert len(_chunked(list(range(2500)), 1000)) == 3


@pytest.mark.asyncio
async def test_finalize_delivery_retries_then_fails(monkeypatch):
    delivery = _delivery(attempt_count=MAX_DELIVERY_ATTEMPTS - 1)
    campaign_id = delivery.campaign_id

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return delivery

    class _Session:
        async def execute(self, *_a, **_k):
            return _Result()

        def add(self, obj):
            pass

        async def commit(self):
            pass

    class _FinalizeSessionCtx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *args):
            return False

    completed = {"called": False}

    async def _maybe_complete(session, cid):
        completed["called"] = True

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.async_session_factory",
        lambda: _FinalizeSessionCtx(),
    )
    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.maybe_complete_campaign",
        _maybe_complete,
    )

    await _finalize_delivery(
        delivery.id,
        campaign_id,
        DeliveryResult(success=False, failure_reason="temporary", retryable=True),
        attempt_count=MAX_DELIVERY_ATTEMPTS - 1,
    )
    assert delivery.status == EmailDeliveryStatus.pending

    delivery.attempt_count = MAX_DELIVERY_ATTEMPTS
    await _finalize_delivery(
        delivery.id,
        campaign_id,
        DeliveryResult(success=False, failure_reason="temporary", retryable=True),
        attempt_count=MAX_DELIVERY_ATTEMPTS,
    )
    assert delivery.status == EmailDeliveryStatus.failed
    assert delivery.failure_reason == "temporary"
    assert completed["called"] is True


@pytest.mark.asyncio
async def test_finalize_batch_marks_all_sent(monkeypatch):
    campaign_id = uuid4()
    d1 = _delivery(campaign_id=campaign_id, attempt_count=1)
    d2 = _delivery(campaign_id=campaign_id, attempt_count=1)
    rows = {d1.id: d1, d2.id: d2}

    class _Scalars:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _Result:
        def __init__(self, items):
            self._items = items

        def scalars(self):
            return _Scalars(self._items)

    class _Session:
        async def execute(self, stmt):
            return _Result([rows[d1.id], rows[d2.id]])

        def add(self, obj):
            pass

        async def commit(self):
            pass

    class _FinalizeSessionCtx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *args):
            return False

    async def _maybe_complete(session, cid):
        pass

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.async_session_factory",
        lambda: _FinalizeSessionCtx(),
    )
    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.maybe_complete_campaign",
        _maybe_complete,
    )

    await _finalize_batch(
        [d1, d2],
        campaign_id,
        BulkBatchSendResult(success=True, sendgrid_message_id="sg-batch", recipient_count=2),
    )
    assert d1.status == EmailDeliveryStatus.sent
    assert d2.status == EmailDeliveryStatus.sent
    assert d1.sendgrid_message_id == "sg-batch"
    assert d2.sendgrid_message_id == "sg-batch"


@pytest.mark.asyncio
async def test_finalize_batch_retry_on_failure(monkeypatch):
    campaign_id = uuid4()
    d1 = _delivery(campaign_id=campaign_id, attempt_count=1)
    d2 = _delivery(campaign_id=campaign_id, attempt_count=MAX_DELIVERY_ATTEMPTS)

    class _Scalars:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _Result:
        def __init__(self, items):
            self._items = items

        def scalars(self):
            return _Scalars(self._items)

    class _Session:
        async def execute(self, stmt):
            return _Result([d1, d2])

        def add(self, obj):
            pass

        async def commit(self):
            pass

    class _FinalizeSessionCtx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.async_session_factory",
        lambda: _FinalizeSessionCtx(),
    )

    async def _maybe_complete(*_a, **_k):
        return None

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.maybe_complete_campaign",
        _maybe_complete,
    )

    await _finalize_batch(
        [d1, d2],
        campaign_id,
        BulkBatchSendResult(
            success=False,
            failure_reason="SendGrid 500",
            retryable=True,
            recipient_count=2,
        ),
    )
    assert d1.status == EmailDeliveryStatus.pending
    assert d1.failure_reason == "SendGrid 500"
    assert d2.status == EmailDeliveryStatus.failed
    assert d2.failure_reason == "SendGrid 500"


@pytest.mark.asyncio
async def test_attachment_bytes_loaded_and_batched(monkeypatch):
    key = f"email-campaigns/tmp/{uuid4()}/file.pdf"
    campaign = _campaign(
        attachments=[
            {
                "file_name": "file.pdf",
                "storage_key": key,
                "content_type": "application/pdf",
            }
        ]
    )
    delivery = _delivery(campaign_id=campaign.id)
    captured = {}

    class _Storage:
        def download(self, storage_key: str) -> bytes:
            assert storage_key == key
            return b"pdf-bytes"

    async def _claim(session, limit=SENDGRID_MAX_PERSONALIZATIONS):
        return [delivery]

    async def _fake_batch(**kwargs):
        captured["attachments"] = kwargs.get("attachments")
        captured["recipients"] = kwargs.get("recipients")
        return BulkBatchSendResult(success=True, sendgrid_message_id="m2", recipient_count=1)

    _patch_delivery_pipeline(
        monkeypatch, claim=_claim, campaign=campaign, fake_batch=_fake_batch
    )
    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.get_bulk_send_storage",
        lambda: _Storage(),
    )

    await process_pending_bulk_emails(limit=1000)
    assert captured["attachments"][0]["content"] == b"pdf-bytes"
    assert captured["attachments"][0]["file_name"] == "file.pdf"
    assert captured["recipients"][0]["delivery_id"] == delivery.id
    assert captured["recipients"][0]["campaign_id"] == campaign.id


@pytest.mark.asyncio
async def test_attachments_downloaded_once_per_campaign_batch(monkeypatch):
    key = f"email-campaigns/tmp/{uuid4()}/file.pdf"
    campaign = _campaign(
        attachments=[
            {
                "file_name": "file.pdf",
                "storage_key": key,
                "content_type": "application/pdf",
            }
        ]
    )
    deliveries = [
        _delivery(campaign_id=campaign.id, email=f"u{i}@example.com", attempt_count=1)
        for i in range(3)
    ]
    downloads = {"n": 0}

    class _Storage:
        def download(self, storage_key: str) -> bytes:
            downloads["n"] += 1
            return b"pdf-bytes"

    async def _claim(session, limit=SENDGRID_MAX_PERSONALIZATIONS):
        return deliveries

    async def _fake_batch(**kwargs):
        return BulkBatchSendResult(
            success=True,
            sendgrid_message_id="m",
            recipient_count=len(kwargs["recipients"]),
        )

    _patch_delivery_pipeline(
        monkeypatch, claim=_claim, campaign=campaign, fake_batch=_fake_batch
    )
    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.get_bulk_send_storage",
        lambda: _Storage(),
    )

    await process_pending_bulk_deliveries(limit=1000)
    assert downloads["n"] == 1


def test_load_attachment_payloads_caches():
    key = "email-campaigns/tmp/x/a.pdf"
    campaign = _campaign(
        attachments=[{"file_name": "a.pdf", "storage_key": key, "content_type": "application/pdf"}]
    )
    calls = {"n": 0}

    class _Storage:
        def download(self, storage_key: str) -> bytes:
            calls["n"] += 1
            return b"data"

    cache: dict = {}
    first = _load_attachment_payloads(campaign, _Storage(), cache)  # type: ignore[arg-type]
    second = _load_attachment_payloads(campaign, _Storage(), cache)  # type: ignore[arg-type]
    assert first[0]["content"] == b"data"
    assert second[0]["content"] == b"data"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_transactional_processor_does_not_call_bulk(monkeypatch, mock_db):
    called = {"bulk": False}

    async def _bulk(limit=None):
        called["bulk"] = True
        return 0

    monkeypatch.setattr("core.email_service.process_pending_bulk_emails", _bulk)
    from unittest.mock import AsyncMock

    session = mock_db()
    context = AsyncMock()
    context.__aenter__.return_value = session
    processed = await process_pending_emails(limit=1, session_factory=lambda: context)
    assert processed == 0
    session.execute.assert_awaited_once()
    assert called["bulk"] is False


@pytest.mark.asyncio
async def test_transactional_cron_does_not_process_bulk(monkeypatch):
    calls = {"tx": 0, "bulk": 0}

    async def _tx(limit=10, *, session_factory=None, lease_owner=None):
        calls["tx"] += 1
        return 0

    async def _bulk(limit=None):
        calls["bulk"] += 1
        return 0

    async def _producer(*, session_factory=None):
        return 0

    monkeypatch.setattr("core.email_service.process_pending_emails", _tx)
    monkeypatch.setattr("core.email_service.process_pending_bulk_emails", _bulk)
    monkeypatch.setattr(
        "apps.connections.services.connection_reminder_service.process_connection_reminders",
        _producer,
    )
    monkeypatch.setattr(
        "apps.profiles.services.graduation_email_service.process_graduation_completion_emails",
        _producer,
    )

    await cron_send_emails()
    assert calls["tx"] == 1
    assert calls["bulk"] == 0


@pytest.mark.asyncio
async def test_bulk_cron_processes_pending_deliveries(monkeypatch):
    calls = {"n": 0, "limit": None}

    async def _process(limit=SENDGRID_MAX_PERSONALIZATIONS, *, session_factory=None):
        calls["n"] += 1
        calls["limit"] = limit
        return 0

    monkeypatch.setattr(
        "apps.bulk_send.cron.process_pending_bulk_deliveries",
        _process,
    )

    await cron_send_bulk_emails()

    assert calls["n"] == 1
    assert calls["limit"] == SENDGRID_MAX_PERSONALIZATIONS
    assert BULK_EMAIL_CRON_INTERVAL_SECONDS == 60


@pytest.mark.asyncio
async def test_bulk_cron_propagates_iteration_errors(monkeypatch):
    ticks = {"n": 0}

    async def _process(limit=SENDGRID_MAX_PERSONALIZATIONS, *, session_factory=None):
        ticks["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "apps.bulk_send.cron.process_pending_bulk_deliveries",
        _process,
    )

    with pytest.raises(RuntimeError, match="boom"):
        await cron_send_bulk_emails()

    assert ticks["n"] == 1


@pytest.mark.asyncio
async def test_sendgrid_bulk_batch_builds_personalizations(monkeypatch):
    from core.email_service import send_bulk_campaign_batch

    captured = {}

    class _FakeMail:
        def __init__(self):
            self.personalizations = []
            self.from_email = None
            self.subject = None
            self._contents = []
            self.attachments = []

        def add_content(self, content):
            self._contents.append(content)

        def add_personalization(self, personalization):
            self.personalizations.append(personalization)

        def add_attachment(self, attachment):
            self.attachments.append(attachment)

    class _FakePersonalization:
        def __init__(self):
            self.tos = []
            self.custom_args = {}

        def add_to(self, to):
            self.tos.append(to)

        def add_custom_arg(self, arg):
            self.custom_args[arg.key] = arg.value

    class _FakeResponse:
        status_code = 202
        headers = {"X-Message-Id": "sg-batch-99"}

    class _FakeClient:
        def __init__(self, api_key):
            captured["api_key"] = api_key

        def send(self, message):
            captured["message"] = message
            return _FakeResponse()

    import core.email_service as email_svc

    monkeypatch.setattr(email_svc.email_settings, "sendgrid_api_key", "SG.test-key")
    monkeypatch.setattr(email_svc.email_settings, "sendgrid_from_email", "from@example.com")
    monkeypatch.setattr(email_svc, "SendGridAPIClient", _FakeClient, raising=False)

    import sendgrid
    import sendgrid.helpers.mail as mail_helpers

    monkeypatch.setattr(sendgrid, "SendGridAPIClient", _FakeClient)
    monkeypatch.setattr(mail_helpers, "Mail", _FakeMail)
    monkeypatch.setattr(mail_helpers, "Personalization", _FakePersonalization)

    campaign_id = uuid4()
    d1 = uuid4()
    d2 = uuid4()
    result = await send_bulk_campaign_batch(
        subject="Hi",
        html_body="<p>Hi</p>",
        plain_text="Hi",
        recipients=[
            {"email": "a@example.com", "campaign_id": campaign_id, "delivery_id": d1},
            {"email": "b@example.com", "campaign_id": campaign_id, "delivery_id": d2},
        ],
        attachments=[{"content": b"x", "file_name": "x.txt", "content_type": "text/plain"}],
    )
    assert result.success is True
    assert result.sendgrid_message_id == "sg-batch-99"
    assert result.recipient_count == 2
    message = captured["message"]
    assert message.subject == "Hi"
    assert len(message.personalizations) == 2
    assert message.personalizations[0].custom_args["campaign_id"] == str(campaign_id)
    assert message.personalizations[0].custom_args["delivery_id"] == str(d1)
    assert message.personalizations[1].custom_args["delivery_id"] == str(d2)
    # One personalization per delivery — not a single multi-to personalization.
    assert len(message.personalizations[0].tos) == 1
    assert len(message.personalizations[1].tos) == 1
    assert message.personalizations[0].tos[0].email == "a@example.com"
    assert message.personalizations[1].tos[0].email == "b@example.com"
    assert len(message._contents) == 2
    plain = message._contents[0]
    html = message._contents[1]
    assert getattr(plain, "mime_type", None) == "text/plain" or str(plain).find("text/plain") >= 0
    assert getattr(html, "mime_type", None) == "text/html" or str(html).find("text/html") >= 0
    assert getattr(plain, "content", None) == "Hi" or "Hi" in str(plain)
    assert getattr(html, "content", None) == "<p>Hi</p>" or "<p>Hi</p>" in str(html)


@pytest.mark.asyncio
async def test_sendgrid_bulk_helper_returns_message_id(monkeypatch):
    from core.email_service import send_bulk_campaign_email

    async def _deliver(*args, **kwargs):
        return True, None, "sg-123"

    monkeypatch.setattr("core.email_service._deliver_email_via_sendgrid", _deliver)
    result = await send_bulk_campaign_email(
        to_email="a@example.com",
        subject="Hi",
        html_body="<p>Hi</p>",
        attachments=[{"content": b"x", "file_name": "x.txt", "content_type": "text/plain"}],
    )
    assert result.success is True
    assert result.sendgrid_message_id == "sg-123"


@pytest.mark.asyncio
async def test_log_bulk_email_dispatch_activity_delivered(monkeypatch):
    from apps.bulk_send.delivery_service import log_bulk_email_dispatch_activity
    from apps.bulk_send.schemas import DeliveryStats

    captured = {}

    async def _create_log(*_a, **kwargs):
        captured.update(kwargs)

    async def _role(_session, _user_id):
        return "superadmin"

    monkeypatch.setattr(
        "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
        _create_log,
    )
    monkeypatch.setattr(
        "apps.bulk_send.delivery_service._actor_role_for_user",
        _role,
    )

    admin_id = uuid4()
    campaign = _campaign(
        created_by=admin_id,
        status=EmailCampaignStatus.completed,
        name="August",
        subject="Update",
    )
    stats = DeliveryStats(total=2, pending=0, processing=0, sent=2, failed=0)
    await log_bulk_email_dispatch_activity(object(), campaign, stats)

    assert captured["user_id"] == admin_id
    assert captured["role"] == "superadmin"
    assert captured["action"] == "create"
    assert captured["module"] == "bulk_email"
    assert captured["description"] == "sent a bulk email"
    assert captured["metadata"]["new"]["status"] == "delivered"
    assert captured["metadata"]["new"]["sent"] == 2
    assert captured["commit"] is True


@pytest.mark.asyncio
async def test_log_bulk_email_dispatch_activity_failed(monkeypatch):
    from apps.bulk_send.delivery_service import log_bulk_email_dispatch_activity
    from apps.bulk_send.schemas import DeliveryStats

    captured = {}

    async def _create_log(*_a, **kwargs):
        captured.update(kwargs)

    async def _role(_session, _user_id):
        return "moderator"

    monkeypatch.setattr(
        "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
        _create_log,
    )
    monkeypatch.setattr(
        "apps.bulk_send.delivery_service._actor_role_for_user",
        _role,
    )

    campaign = _campaign(
        created_by=uuid4(),
        status=EmailCampaignStatus.failed,
        name="August",
        subject="Update",
    )
    stats = DeliveryStats(total=2, pending=0, processing=0, sent=0, failed=2)
    await log_bulk_email_dispatch_activity(object(), campaign, stats)

    assert captured["action"] == "fail"
    assert captured["role"] == "moderator"
    assert captured["description"] == "failed to send a bulk email"
    assert captured["metadata"]["new"]["status"] == "failed"
    assert captured["metadata"]["new"]["failed"] == 2


@pytest.mark.asyncio
async def test_finalize_batch_writes_activity_log_when_complete(monkeypatch):
    from apps.bulk_send.repository import CampaignTerminalResult
    from apps.bulk_send.schemas import DeliveryStats

    campaign_id = uuid4()
    campaign = _campaign(id=campaign_id, created_by=uuid4(), status=EmailCampaignStatus.completed)
    d1 = _delivery(campaign_id=campaign_id, attempt_count=1)

    class _Scalars:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _Result:
        def __init__(self, items):
            self._items = items

        def scalars(self):
            return _Scalars(self._items)

    class _Session:
        async def execute(self, stmt):
            return _Result([d1])

        def add(self, obj):
            pass

        async def commit(self):
            pass

    class _FinalizeSessionCtx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *args):
            return False

    stats = DeliveryStats(total=1, pending=0, processing=0, sent=1, failed=0)
    logged = {}

    async def _maybe_complete(session, cid):
        return CampaignTerminalResult(campaign=campaign, stats=stats)

    async def _log(session, result_campaign, result_stats):
        logged["campaign"] = result_campaign
        logged["stats"] = result_stats

    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.async_session_factory",
        lambda: _FinalizeSessionCtx(),
    )
    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.maybe_complete_campaign",
        _maybe_complete,
    )
    monkeypatch.setattr(
        "apps.bulk_send.delivery_service.log_bulk_email_dispatch_activity",
        _log,
    )

    await _finalize_batch(
        [d1],
        campaign_id,
        BulkBatchSendResult(success=True, sendgrid_message_id="sg-1", recipient_count=1),
    )
    assert logged["campaign"] is campaign
    assert logged["stats"].sent == 1
