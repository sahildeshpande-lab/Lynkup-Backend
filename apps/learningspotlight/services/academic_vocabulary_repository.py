"""Load canonical academic vocabulary from existing KampuLynk catalog tables."""

from __future__ import annotations

import inspect
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.db_models.major_db_model import Major
from apps.profiles.db_models.minor_db_model import Minor


def dedupe_canonical_terms(terms: Iterable[str]) -> list[str]:
    """Deduplicate catalog names case-insensitively while preserving first casing."""
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        cleaned = " ".join(str(term).strip().split())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


async def load_canonical_academic_terms(session: AsyncSession) -> list[str]:
    """Load active major, minor, and academic-interest names from the database."""
    major_names = await _scalar_names(session, select(Major.name).where(Major.is_active.is_(True)))
    minor_names = await _scalar_names(session, select(Minor.name).where(Minor.is_active.is_(True)))
    interest_names = await _scalar_names(
        session,
        select(AcademicInterest.name).where(AcademicInterest.is_active.is_(True)),
    )
    return dedupe_canonical_terms([*major_names, *minor_names, *interest_names])


async def _maybe_await(value):
    # Only await real coroutines. MagicMock objects can look awaitable via
    # auto-created ``__await__`` and would hang if awaited.
    if inspect.iscoroutine(value):
        return await value
    return value


async def _scalar_names(session: AsyncSession, stmt) -> list[str]:
    try:
        result = await _maybe_await(session.execute(stmt))
        scalar_result = await _maybe_await(result.scalars())
        values = await _maybe_await(scalar_result.all())
    except Exception:
        return []
    if not isinstance(values, (list, tuple, set)):
        return []
    return [str(value) for value in values]
