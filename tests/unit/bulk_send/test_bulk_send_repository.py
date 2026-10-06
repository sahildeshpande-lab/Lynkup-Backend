from __future__ import annotations

from uuid import uuid4

import pytest

from apps.bulk_send.enums import EmailCampaignStatus, EmailDeliveryStatus
from apps.bulk_send.repository import (
    _campaign_search_clause,
    claim_pending_deliveries,
    maybe_complete_campaign,
)
from apps.bulk_send.schemas import DeliveryStats


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values

    def first(self):
        return self._values[0] if self._values else None


class _Result:
    def __init__(self, values=None, value=None, row=None):
        self._values = values or []
        self._value = value
        self._row = row

    def scalars(self):
        return _Scalars(self._values)

    def scalar_one(self):
        return self._value

    def one(self):
        return self._row

    def all(self):
        return self._values


@pytest.mark.asyncio
async def test_claim_pending_deliveries_marks_processing():
    d1 = type(
        "D",
        (),
        {
            "id": uuid4(),
            "status": EmailDeliveryStatus.pending,
            "attempt_count": 0,
            "last_attempt_at": None,
            "updated_at": None,
        },
    )()
    d2 = type(
        "D",
        (),
        {
            "id": uuid4(),
            "status": EmailDeliveryStatus.pending,
            "attempt_count": 0,
            "last_attempt_at": None,
            "updated_at": None,
        },
    )()

    class _Session:
        def __init__(self):
            self.added = []
            self.committed = False
            self._calls = 0

        async def execute(self, stmt):
            self._calls += 1
            return _Result(values=[d1, d2])

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            self.committed = True

        async def refresh(self, obj):
            return None

        async def rollback(self):
            return None

    session = _Session()
    claimed = await claim_pending_deliveries(session, limit=10)  # type: ignore[arg-type]
    assert len(claimed) == 2
    assert d1.status == EmailDeliveryStatus.processing
    assert d1.attempt_count == 1
    assert d2.attempt_count == 1
    assert session.committed is True


@pytest.mark.asyncio
async def test_claim_pending_deliveries_uses_skip_locked():
    from sqlalchemy.dialects import postgresql

    captured = {}

    class _Session:
        async def execute(self, stmt):
            captured["stmt"] = stmt
            return _Result(values=[])

        async def rollback(self):
            return None

        def add(self, obj):
            return None

        async def commit(self):
            return None

        async def refresh(self, obj):
            return None

    session = _Session()
    claimed = await claim_pending_deliveries(session, limit=10)  # type: ignore[arg-type]
    assert claimed == []
    sql = str(
        captured["stmt"].compile(dialect=postgresql.dialect())
    ).lower()
    assert "for update" in sql
    assert "skip locked" in sql
    assert "email_deliveries" in sql


@pytest.mark.asyncio
async def test_second_claim_does_not_reclaim_locked_delivery():
    delivery = type(
        "D",
        (),
        {
            "id": uuid4(),
            "status": EmailDeliveryStatus.pending,
            "attempt_count": 0,
            "last_attempt_at": None,
            "updated_at": None,
        },
    )()
    remaining = [delivery]

    class _Session:
        def __init__(self):
            self.committed = 0

        async def execute(self, stmt):
            values = list(remaining)
            remaining.clear()
            return _Result(values=values)

        def add(self, obj):
            return None

        async def commit(self):
            self.committed += 1

        async def refresh(self, obj):
            return None

        async def rollback(self):
            return None

    session = _Session()
    first = await claim_pending_deliveries(session, limit=10)  # type: ignore[arg-type]
    second = await claim_pending_deliveries(session, limit=10)  # type: ignore[arg-type]
    assert len(first) == 1
    assert first[0].status == EmailDeliveryStatus.processing
    assert second == []
    assert session.committed == 1



@pytest.mark.asyncio
async def test_maybe_complete_campaign_when_terminal(monkeypatch):
    campaign = type(
        "C",
        (),
        {
            "id": uuid4(),
            "status": EmailCampaignStatus.processing,
            "completed_at": None,
            "updated_at": None,
        },
    )()

    class _Session:
        def __init__(self):
            self.committed = False

        def add(self, obj):
            return None

        async def commit(self):
            self.committed = True

    async def _stats(session, campaign_id):
        return DeliveryStats(total=2, pending=0, processing=0, sent=1, failed=1)

    async def _get(session, campaign_id):
        return campaign

    monkeypatch.setattr(
        "apps.bulk_send.repository.delivery_stats_for_campaign",
        _stats,
    )
    monkeypatch.setattr("apps.bulk_send.repository.get_campaign", _get)

    session = _Session()
    result = await maybe_complete_campaign(session, campaign.id)  # type: ignore[arg-type]
    assert campaign.status == EmailCampaignStatus.completed
    assert campaign.completed_at is not None
    assert session.committed is True
    assert result is not None
    assert result.campaign is campaign
    assert result.stats.sent == 1
    assert result.stats.failed == 1


@pytest.mark.asyncio
async def test_maybe_complete_campaign_marks_failed_when_all_failed(monkeypatch):
    campaign = type(
        "C",
        (),
        {
            "id": uuid4(),
            "status": EmailCampaignStatus.processing,
            "completed_at": None,
            "updated_at": None,
        },
    )()

    class _Session:
        def add(self, obj):
            return None

        async def commit(self):
            return None

    async def _stats(session, campaign_id):
        return DeliveryStats(total=2, pending=0, processing=0, sent=0, failed=2)

    async def _get(session, campaign_id):
        return campaign

    monkeypatch.setattr(
        "apps.bulk_send.repository.delivery_stats_for_campaign",
        _stats,
    )
    monkeypatch.setattr("apps.bulk_send.repository.get_campaign", _get)

    result = await maybe_complete_campaign(_Session(), campaign.id)  # type: ignore[arg-type]
    assert campaign.status == EmailCampaignStatus.failed
    assert campaign.completed_at is not None
    assert result is not None
    assert result.stats.failed == 2


@pytest.mark.asyncio
async def test_maybe_complete_campaign_skips_already_failed(monkeypatch):
    campaign = type(
        "C",
        (),
        {
            "id": uuid4(),
            "status": EmailCampaignStatus.failed,
            "completed_at": None,
            "updated_at": None,
        },
    )()

    class _Session:
        def add(self, obj):
            raise AssertionError("should not update a failed campaign")

        async def commit(self):
            raise AssertionError("should not commit a failed campaign")

    async def _stats(session, campaign_id):
        return DeliveryStats(total=1, pending=0, processing=0, sent=0, failed=1)

    async def _get(session, campaign_id):
        return campaign

    monkeypatch.setattr(
        "apps.bulk_send.repository.delivery_stats_for_campaign",
        _stats,
    )
    monkeypatch.setattr("apps.bulk_send.repository.get_campaign", _get)

    result = await maybe_complete_campaign(_Session(), campaign.id)  # type: ignore[arg-type]
    assert result is None
    assert campaign.status == EmailCampaignStatus.failed


def test_campaign_search_clause_is_none_for_blank():
    assert _campaign_search_clause(None) is None
    assert _campaign_search_clause("   ") is None


def test_campaign_search_clause_matches_name_subject_and_body():
    from sqlalchemy.dialects import postgresql

    clause = _campaign_search_clause("welcome")
    sql = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "lower" in sql
    assert "like" in sql
    assert "%welcome%" in sql
    assert "name" in sql
    assert "subject" in sql
    assert "body_html" not in sql
    assert "body_text" in sql


def test_campaign_search_clause_handles_spaces_and_lowercases_query():
    from sqlalchemy.dialects import postgresql

    clause = _campaign_search_clause("Bulk Email")
    sql = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "lower" in sql
    assert "like" in sql
    assert "%bulk email%" in sql
    assert "subject" in sql


def test_campaign_search_clause_coalesces_body_text():
    from sqlalchemy.dialects import postgresql

    clause = _campaign_search_clause("HELLO")
    sql = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "coalesce" in sql
    assert "body_text" in sql
    assert "lower" in sql
    assert "%hello%" in sql
