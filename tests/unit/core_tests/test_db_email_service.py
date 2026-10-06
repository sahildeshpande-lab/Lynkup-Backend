from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest

from apps.administration.db_models.template_db_model import Template
from apps.administration.initial_templates import INITIAL_TEMPLATES
from apps.administration.services.template_service import (
    get_template_by_name,
    render_email_by_name,
    render_template,
)
from common.exceptions import ApiError
from core.email_service import (
    _build_otp_display_html,
    build_otp_email_html,
    build_graduation_completion_email_html,
    build_account_created_email_html,
)


def _mock_session_with_templates(templates: list[Template]):
    session = AsyncMock()
    
    async def execute(statement, *args, **kwargs):
        mock_res = MagicMock()
        statement_str = str(statement)
        name_val = None
        if hasattr(statement, "_where_criteria") and statement._where_criteria:
            for crit in statement._where_criteria:
                if hasattr(crit, "right") and hasattr(crit.right, "value"):
                    name_val = crit.right.value
        found = [t for t in templates if t.name == name_val]
        mock_res.scalars.return_value.first.return_value = found[0] if found else None
        mock_res.scalar_one_or_none.return_value = found[0] if found else None
        return mock_res

    session.execute = AsyncMock(side_effect=execute)
    return session


@pytest.mark.asyncio
async def test_otp_template_rendering_and_escaping():
    otp_seed = next(t for t in INITIAL_TEMPLATES if t["name"] == "otp_email")
    otp_tpl = Template(
        name="otp_email",
        subject=otp_seed["subject"],
        body_html=otp_seed["body_html"],
        status="active",
    )
    session = _mock_session_with_templates([otp_tpl])

    # 1. Standard OTP replacement
    raw_otp_card = _build_otp_display_html("2913", "#0B5FA5")
    subject, html = await render_email_by_name(
        session,
        "otp_email",
        {
            "first_name": "Sahil",
            "displayotp": raw_otp_card,
            "otp_minutes": 10,
        },
        raw_keys={"displayotp"},
    )
    assert "Hello Sahil" in html
    for d in "2913":
        assert f">{d}</td>" in html
    assert "10 minutes" in html
    assert "{{" not in html  # no unresolved placeholders
    assert '<td class="otp-digit"' in html  # raw HTML preserved for displayotp

    # 2. XSS escaping on first_name
    subject_xss, html_xss = await render_email_by_name(
        session,
        "otp_email",
        {
            "first_name": "<script>alert(1)</script>",
            "displayotp": raw_otp_card,
            "otp_minutes": 5,
        },
        raw_keys={"displayotp"},
    )
    assert "<script>alert(1)</script>" not in html_xss
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_xss


@pytest.mark.asyncio
async def test_graduation_template_rendering():
    grad_seed = next(t for t in INITIAL_TEMPLATES if t["name"] == "graduation_email")
    grad_tpl = Template(
        name="graduation_email",
        subject=grad_seed["subject"],
        body_html=grad_seed["body_html"],
        status="active",
    )
    session = _mock_session_with_templates([grad_tpl])

    subject, html = await render_email_by_name(
        session,
        "graduation_email",
        {
            "first_name": "Alice",
            "university_name": "Stanford University",
        },
    )
    assert subject == "Congratulations on your graduation!"
    assert "graduation from the Stanford University marks an incredible milestone" in html
    assert "<!doctype html>" in html.lower()
    assert "{{" not in html


@pytest.mark.asyncio
async def test_data_export_template_rendering():
    export_seed = next(t for t in INITIAL_TEMPLATES if t["name"] == "data_export_ready_email")
    export_tpl = Template(
        name="data_export_ready_email",
        subject=export_seed["subject"],
        body_html=export_seed["body_html"],
        status="active",
    )
    session = _mock_session_with_templates([export_tpl])

    subject, html = await render_email_by_name(
        session,
        "data_export_ready_email",
        {
            "first_name": "Bob",
            "download_url": "https://spaces.kampulynk.com/export.zip?token=xyz",
            "zip_password": "PASSWD",
            "zip_filename": "export_2026.zip",
            "download_expires_at": "30 September 2026",
        },
        raw_keys={"download_url"},
    )
    assert "PASSWD" in html
    assert "export_2026.zip" in html
    assert "30 September 2026" in html
    assert 'href="https://spaces.kampulynk.com/export.zip?token=xyz"' in html
    assert "{{" not in html


@pytest.mark.asyncio
async def test_missing_template_error():
    session = _mock_session_with_templates([])

    with pytest.raises(ApiError) as exc_info:
        await render_email_by_name(session, "non_existent_random_template_xyz", {})
    assert "not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_inactive_template_error():
    inactive_tpl = Template(
        name="inactive_tpl",
        subject="Inactive",
        body_html="<p>Test</p>",
        status="inactive",
    )
    session = _mock_session_with_templates([inactive_tpl])

    with pytest.raises(ApiError) as exc_info:
        await render_email_by_name(session, "inactive_tpl", {})
    assert "is inactive" in str(exc_info.value)


@pytest.mark.asyncio
async def test_missing_template_variables_validation():
    tpl = Template(
        name="test_missing_vars",
        subject="Test {{subject_var}}",
        body_html="<p>Hello {{required_var}} and {{another_var}}</p>",
        status="active",
    )
    session = _mock_session_with_templates([tpl])

    # Missing 'another_var'
    with pytest.raises(ApiError) as exc_info:
        await render_email_by_name(
            session,
            "test_missing_vars",
            {"subject_var": "Sub", "required_var": "Sahil"},
        )
    assert "Missing template variables for 'test_missing_vars'" in str(exc_info.value)
    assert "another_var" in str(exc_info.value)


def test_synchronous_builders_standalone_html():
    # Verify sync builders produce complete HTML with full layout
    otp_html = build_otp_email_html("4829", first_name="Sahil")
    assert "<!doctype html>" in otp_html.lower()
    for d in "4829":
        assert f">{d}</td>" in otp_html
    assert "Kampu" in otp_html and "Lynk" in otp_html

    grad_html = build_graduation_completion_email_html(first_name="Jane", university_name="MIT")
    assert "<!doctype html>" in grad_html.lower()
    assert "MIT" in grad_html

    acc_html = build_account_created_email_html(first_name="Jane")
    assert "<!doctype html>" in acc_html.lower()
    assert "Hello Jane," in acc_html

