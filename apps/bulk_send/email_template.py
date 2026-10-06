"""Branded HTML wrapper for bulk campaign emails (DB template)."""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from apps.administration.services.template_service import render_email_by_name

BULK_CAMPAIGN_TEMPLATE_NAME = "bulk_campaign_email"


def _campaign_body_html(body_html: str) -> str:
    cleaned = (body_html or "").strip()
    if not cleaned:
        return ""
    if re.search(r"<[a-zA-Z/]", cleaned):
        return cleaned
    paragraphs = "".join(
        f'<p style="margin:0 0 12px;">{line}</p>'
        for line in cleaned.splitlines()
        if line.strip()
    )
    return paragraphs or cleaned


async def render_bulk_campaign_html(
    *,
    name: str,
    subject: str,
    body_html: str = "",
    session: AsyncSession | None = None,
) -> str:
    """Render campaign body inside the branded bulk email shell from DB.

    Layout: header → subject bar → body → \"From your friends…\" → footer.
    Requires an active ``bulk_campaign_email`` row in ``templates``.
    """
    _, rendered = await render_email_by_name(
        session,
        BULK_CAMPAIGN_TEMPLATE_NAME,
        {
            "name": name or "",
            "subject": subject or name or "",
            "campaign_body_html": _campaign_body_html(body_html),
        },
        raw_keys={"campaign_body_html"},
    )
    return rendered
