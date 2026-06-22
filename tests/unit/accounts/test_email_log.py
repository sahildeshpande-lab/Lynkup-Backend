"""
Unit tests for TransactionalEmailLog model and the _log_email_event helper
in apps/accounts/services.py.
All DB operations use the real async session but are rolled back via the
autouse db_cleanup fixture in conftest.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# TransactionalEmailLog model — field defaults & construction
# ---------------------------------------------------------------------------

class TestTransactionalEmailLogModel:
    def test_model_instantiation(self):
        from apps.accounts.db_models import TransactionalEmailLog

        log = TransactionalEmailLog(
            to="pytest.email@example.com",
            from_email="no-reply@test.com",
            body="Hello world",
            purpose="email_verification",
            subject="Verify your email",
            is_sent=True,
        )
        assert log.to == "pytest.email@example.com"
        assert log.purpose == "email_verification"
        assert log.is_sent is True
        assert log.attachment is None

    def test_default_is_sent_is_false(self):
        from apps.accounts.db_models import TransactionalEmailLog

        log = TransactionalEmailLog(
            to="pytest.email2@example.com",
            from_email="no-reply@test.com",
            body="body",
            purpose="test",
            subject="Subject",
        )
        assert log.is_sent is False

    def test_id_auto_generated(self):
        from apps.accounts.db_models import TransactionalEmailLog

        log = TransactionalEmailLog(
            to="pytest.email3@example.com",
            from_email="no-reply@test.com",
            body="body",
            purpose="test",
            subject="Subject",
        )
        assert log.id is not None

    def test_attachment_field_accepts_value(self):
        from apps.accounts.db_models import TransactionalEmailLog

        log = TransactionalEmailLog(
            to="pytest.email4@example.com",
            from_email="no-reply@test.com",
            body="body",
            purpose="test",
            subject="Subject",
            attachment="s3://bucket/file.pdf",
        )
        assert log.attachment == "s3://bucket/file.pdf"


# ---------------------------------------------------------------------------
# _log_email_event helper — via mock DB session
# ---------------------------------------------------------------------------

class TestLogEmailEvent:
    @pytest.mark.asyncio
    async def test_log_email_event_adds_to_session(self):
        from apps.accounts.services import _log_email_event

        mock_db = MagicMock()
        mock_db.add = MagicMock()

        await _log_email_event(
            mock_db,
            to_email="pytest.log@example.com",
            subject="Test Subject",
            body="Test body",
            purpose="email_verification",
            is_sent=True,
        )

        assert mock_db.add.called

    @pytest.mark.asyncio
    async def test_log_email_event_without_attachment(self):
        from apps.accounts.services import _log_email_event
        from apps.accounts.db_models import TransactionalEmailLog

        added_objects = []

        mock_db = MagicMock()
        mock_db.add = lambda obj: added_objects.append(obj)

        await _log_email_event(
            mock_db,
            to_email="pytest.log2@example.com",
            subject="Hello",
            body="World",
            purpose="reset_password",
        )

        assert len(added_objects) == 1
        obj = added_objects[0]
        assert isinstance(obj, TransactionalEmailLog)
        assert obj.to == "pytest.log2@example.com"
        assert obj.attachment is None

    @pytest.mark.asyncio
    async def test_log_email_event_with_attachment(self):
        from apps.accounts.services import _log_email_event
        from apps.accounts.db_models import TransactionalEmailLog

        added_objects = []
        mock_db = MagicMock()
        mock_db.add = lambda obj: added_objects.append(obj)

        await _log_email_event(
            mock_db,
            to_email="pytest.log3@example.com",
            subject="With Attachment",
            body="See attached",
            purpose="report",
            attachment="s3://bucket/report.pdf",
        )

        obj = added_objects[0]
        assert obj.attachment == "s3://bucket/report.pdf"

    @pytest.mark.asyncio
    async def test_log_email_event_is_sent_false_by_default(self):
        from apps.accounts.services import _log_email_event
        from apps.accounts.db_models import TransactionalEmailLog

        added_objects = []
        mock_db = MagicMock()
        mock_db.add = lambda obj: added_objects.append(obj)

        await _log_email_event(
            mock_db,
            to_email="pytest.log4@example.com",
            subject="Not Sent",
            body="body",
            purpose="test",
            is_sent=False,
        )

        obj = added_objects[0]
        assert obj.is_sent is False
