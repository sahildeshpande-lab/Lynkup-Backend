from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.administration.db_models.template_db_model import Template
from common.exceptions import ApiError
from core.database.session import async_session_factory
from core.email.config import settings as email_settings

BRAND_COLORS: dict[str, str] = {
    "brand_green": "#46B12F",
    "brand_blue": "#0B5FA5",
    "light_bg": "#F4F7FB",
    "text_primary": "#071A35",
    "text_secondary": "#64748B",
    "footer_bg": "#071A35",
}

_DEFAULT_LOGO_URL = "https://kampulynk-dev-spaces.sfo3.digitaloceanspaces.com/logo/logo.png"
_LOGO_CID = "kampulynk-logo"
_LOGO_FILE = Path(__file__).resolve().parents[3] / "static" / "email" / "kampulynk-logo.png"


def resolve_logo_url(*, prefer_cid: bool = True) -> str:
    """Return logo src for HTML emails."""
    if prefer_cid and _LOGO_FILE.is_file():
        return f"cid:{_LOGO_CID}"

    logo_url = (email_settings.logo_url or os.getenv("LOGO_URL") or "").strip()
    if logo_url.startswith(("http://", "https://")):
        return logo_url
    return _DEFAULT_LOGO_URL


async def get_template_by_name(
    session: AsyncSession,
    name: str,
) -> Template:
    """Retrieve an active template by its unique name."""
    stmt = select(Template).where(Template.name == name)
    template = (await session.execute(stmt)).scalars().first()

    if not template:
        raise ApiError(f"Email template '{name}' not found")

    if template.status != "active":
        raise ApiError(f"Email template '{name}' is inactive")

    return template


def render_template(
    template: Template,
    context: dict[str, Any],
    raw_keys: set[str] | None = None,
) -> tuple[str, str]:
    """Render subject and body_html of a template with the given context.
    
    All normal variables are HTML-escaped by default.
    Only keys listed in `raw_keys` are inserted unescaped.
    Unresolved {{placeholders}} will raise an ApiError.
    """
    raw_keys = raw_keys or set()

    full_context: dict[str, Any] = {
        **BRAND_COLORS,
        "logo_url": resolve_logo_url(prefer_cid=False),
        **context,
    }

    rendered_subject = template.subject
    rendered_body = template.body_html

    for key, value in full_context.items():
        if value is None:
            continue
        placeholder = f"{{{{{key}}}}}"
        if key in raw_keys:
            replacement = str(value)
        else:
            replacement = html.escape(str(value))

        rendered_subject = rendered_subject.replace(placeholder, replacement)
        rendered_body = rendered_body.replace(placeholder, replacement)

    # Check for any unresolved {{variable}} placeholders
    unresolved_subject = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", rendered_subject)
    unresolved_body = re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", rendered_body)
    all_unresolved = []
    seen = set()
    for item in unresolved_subject + unresolved_body:
        if item not in seen:
            seen.add(item)
            all_unresolved.append(item)

    if all_unresolved:
        raise ApiError(
            f"Missing template variables for '{template.name}': {', '.join(all_unresolved)}"
        )

    return rendered_subject, rendered_body



async def render_email_by_name(
    session: AsyncSession | None,
    name: str,
    context: dict[str, Any],
    raw_keys: set[str] | None = None,
) -> tuple[str, str]:
    """Look up template by name and render its subject and body_html."""
    if session is not None:
        template = await get_template_by_name(session, name)
        return render_template(template, context, raw_keys)

    async with async_session_factory() as new_session:
        template = await get_template_by_name(new_session, name)
        return render_template(template, context, raw_keys)


_PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def extract_placeholders(*texts: str | None) -> set[str]:
    """Return unique {{variable}} names found across the given strings."""
    found: set[str] = set()
    for text in texts:
        if text:
            found.update(_PLACEHOLDER_RE.findall(text))
    return found


def required_placeholders_for_template(name: str) -> set[str]:
    """Dynamic placeholders required for a known seeded template name.

    Brand color keys are excluded so admins may hardcode colors. Unknown
    template names have no required set (custom templates are unconstrained).
    """
    from apps.administration.initial_templates import INITIAL_TEMPLATES

    for item in INITIAL_TEMPLATES:
        if item.get("name") != name:
            continue
        required = extract_placeholders(item.get("subject"), item.get("body_html"))
        return required - set(BRAND_COLORS.keys())
    return set()


def missing_required_placeholders(
    name: str,
    *,
    subject: str | None,
    body_html: str | None,
) -> list[str]:
    """Return sorted required placeholder names missing from subject/body."""
    required = required_placeholders_for_template(name)
    if not required:
        return []
    present = extract_placeholders(subject, body_html)
    return sorted(required - present)


def validate_template_placeholders(
    name: str,
    *,
    subject: str | None,
    body_html: str | None,
) -> None:
    """Raise ApiError when required dynamic variables were removed/omitted."""
    missing = missing_required_placeholders(name, subject=subject, body_html=body_html)
    if missing:
        raise ApiError("Failed to save the template")
