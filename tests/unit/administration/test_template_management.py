from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.accounts.db_models import User
from apps.administration.db_models.template_db_model import Template
from core.database.session import get_session
from core.security.auth import get_current_admin
from entrypoints.api import app

client = TestClient(app)


class InMemoryTemplateSession:
    def __init__(self, templates: dict | None = None):
        self.templates = templates if templates is not None else {}
        self.activity_logs = []

    async def execute(self, statement, *args, **kwargs):
        # Handle select query
        mock_result = MagicMock()
        statement_str = str(statement)
        
        # Activity-log actor first_name lookup
        if "profiles.first_name" in statement_str.lower() or "profile.first_name" in statement_str.lower():
            mock_result.first.return_value = None
            return mock_result

        # Check if querying by name
        if "templates.name =" in statement_str:
            name_val = None
            if hasattr(statement, "_where_criteria") and statement._where_criteria:
                for crit in statement._where_criteria:
                    if hasattr(crit, "right") and hasattr(crit.right, "value"):
                        name_val = crit.right.value
            found = [t for t in self.templates.values() if t.name == name_val]
            mock_result.scalars.return_value.first.return_value = found[0] if found else None
            mock_result.scalars.return_value.all.return_value = found
            mock_result.scalar_one_or_none.return_value = found[0] if found else None
            return mock_result

        # Check if querying by id
        if "templates.id =" in statement_str:
            id_val = None
            if hasattr(statement, "_where_criteria") and statement._where_criteria:
                for crit in statement._where_criteria:
                    if hasattr(crit, "right") and hasattr(crit.right, "value"):
                        id_val = crit.right.value
            found = [t for t in self.templates.values() if str(t.id) == str(id_val)]
            mock_result.scalars.return_value.first.return_value = found[0] if found else None
            mock_result.scalars.return_value.all.return_value = found
            mock_result.scalar_one_or_none.return_value = found[0] if found else None
            return mock_result

        # Default list all
        all_templates = list(self.templates.values())
        mock_result.scalars.return_value.first.return_value = all_templates[0] if all_templates else None
        mock_result.scalars.return_value.all.return_value = all_templates
        mock_result.scalar_one_or_none.return_value = all_templates[0] if all_templates else None
        mock_result.first.return_value = None
        return mock_result

    def add(self, instance):
        if isinstance(instance, Template):
            self.templates[str(instance.id)] = instance
        else:
            self.activity_logs.append(instance)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, instance):
        pass

    async def delete(self, instance):
        if isinstance(instance, Template):
            self.templates.pop(str(instance.id), None)


@pytest.fixture
def mock_admin():
    return User(
        id=uuid4(),
        email="admin@example.com",
        role="superadmin",
        first_name="Admin",
        last_name="User",
    )


def test_template_crud_endpoints(mock_admin, monkeypatch):
    db_store = {}
    session = InMemoryTemplateSession(db_store)
    activity_calls: list[dict] = []

    async def _fake_activity_log(db, **kwargs):
        activity_calls.append(kwargs)
        return None

    monkeypatch.setattr(
        "apps.administration.services.admin_activity_log_service.create_admin_activity_log",
        _fake_activity_log,
    )

    app.dependency_overrides[get_current_admin] = lambda: mock_admin
    app.dependency_overrides[get_session] = lambda: session

    try:
        # 1. POST create template
        create_payload = {
            "name": "otp_email",
            "subject": "Your KampuLynk Verification Code",
            "body_html": (
                "<!doctype html><html><body>"
                "<p>Hello {{first_name}}</p>"
                "<p>Code: {{displayotp}}</p>"
                "<p>Expires in {{otp_minutes}} minutes</p>"
                "<img src=\"{{logo_url}}\" alt=\"logo\">"
                "</body></html>"
            ),
            "status": "active",
        }
        res = client.post("/api/v1/admin/templates", json=create_payload)
        assert res.status_code == 201, res.text
        data = res.json()["data"]
        template_id = data["id"]
        assert data["name"] == "otp_email"
        assert data["subject"] == "Your KampuLynk Verification Code"
        assert data["status"] == "active"
        assert data["updated_by"] == str(mock_admin.id)

        # 1b. Reject create when required dynamic variables are missing
        incomplete_create = {
            "name": "email_verified_email",
            "subject": "KampuLynk Email Verified",
            "body_html": "<html><body><p>Verified — no placeholders</p></body></html>",
            "status": "active",
        }
        incomplete_res = client.post("/api/v1/admin/templates", json=incomplete_create)
        assert incomplete_res.status_code == 200
        assert incomplete_res.json()["status"] is False
        assert incomplete_res.json()["message"] == "Failed to save the template"

        # 2. Duplicate name error
        dup_res = client.post("/api/v1/admin/templates", json=create_payload)
        assert dup_res.status_code == 200
        assert dup_res.json()["status"] is False
        assert "already exists" in dup_res.json()["message"]

        # 3. GET all templates (summary list, without body_html)
        list_res = client.get("/api/v1/admin/templates")
        assert list_res.status_code == 200
        items = list_res.json()["data"]
        assert len(items) == 1
        assert items[0]["name"] == "otp_email"
        assert "body_html" not in items[0]

        # 4. GET by name (returns full template with body_html)
        get_res = client.get("/api/v1/admin/templates?name=otp_email")
        assert get_res.status_code == 200
        get_data = get_res.json()["data"]
        assert get_data["name"] == "otp_email"
        assert get_data["body_html"] == create_payload["body_html"]

        # 5. GET missing template
        missing_res = client.get("/api/v1/admin/templates?name=non_existent")
        assert missing_res.status_code == 200
        assert missing_res.json()["status"] is False
        assert "not found" in missing_res.json()["message"]

        # 6. PATCH update template
        patch_payload = {
            "template_id": template_id,
            "subject": "Updated Verification Code",
            "status": "inactive",
        }
        patch_res = client.patch("/api/v1/admin/templates", json=patch_payload)
        assert patch_res.status_code == 200
        patch_data = patch_res.json()["data"]
        assert patch_data["subject"] == "Updated Verification Code"
        assert patch_data["status"] == "inactive"
        assert patch_data["name"] == "otp_email"  # Name remains unchanged

        # 6b. PATCH rejects body that removes required dynamic variables
        bad_patch = {
            "template_id": template_id,
            "body_html": "<html><body><p>No dynamic variables left</p></body></html>",
        }
        bad_patch_res = client.patch("/api/v1/admin/templates", json=bad_patch)
        assert bad_patch_res.status_code == 200
        assert bad_patch_res.json()["status"] is False
        assert bad_patch_res.json()["message"] == "Failed to save the template"
        # Unchanged after rejected patch
        get_after_bad = client.get("/api/v1/admin/templates?name=otp_email")
        assert "{{displayotp}}" in get_after_bad.json()["data"]["body_html"]

        # 7. DELETE template
        del_res = client.delete(f"/api/v1/admin/templates/{template_id}")
        assert del_res.status_code == 200
        assert del_res.json()["message"] == "Template deleted successfully"

        # Verify deletion
        assert template_id not in db_store

        assert [c["description"] for c in activity_calls] == [
            "created the Email Otp otp_email",
            "updated the Email Otp otp_email",
            "deleted the Email Otp otp_email",
        ]
        assert all(c["module"] == "email_template" for c in activity_calls)
        assert [c["action"] for c in activity_calls] == ["create", "update", "delete"]
    finally:
        app.dependency_overrides.pop(get_current_admin, None)
        app.dependency_overrides.pop(get_session, None)


def test_format_email_template_activity_description():
    from apps.administration.services.admin_activity_log_service import (
        format_email_template_activity_description,
        format_email_template_activity_label,
    )

    assert format_email_template_activity_label("otp_email") == "Email Otp"
    assert (
        format_email_template_activity_description("updated", "otp_email")
        == "updated the Email Otp otp_email"
    )
    assert (
        format_email_template_activity_description("created", "reset_password_email")
        == "created the Reset Password Email reset_password_email"
    )
