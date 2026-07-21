from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

# Near-duplicate threshold for pg_trgm similarity (case-insensitive).
# Catches casing and minor spelling variants (e.g. "Artificial intelligence"
# vs "Artificial Intelligence", "Deep learnig" vs "Deep learning") without
# collapsing clearly distinct interests (e.g. "NLP" vs "Web Development").
_ACADEMIC_INTEREST_SIMILARITY_THRESHOLD = 0.7


def _normalize_academic_interest_name(name: str) -> str:
    return " ".join(name.split()).lower()


async def _find_existing_academic_interest(name: str, db: AsyncSession):
    """Return an existing interest that matches exactly (case-insensitive) or is a near-duplicate."""
    from apps.profiles.db_models.academic_interests_db_model import AcademicInterest

    normalized = _normalize_academic_interest_name(name)
    if not normalized:
        return None

    name_lower = func.lower(func.trim(AcademicInterest.name))
    similarity_score = func.similarity(name_lower, normalized)

    return (
        await db.execute(
            select(AcademicInterest)
            .where(
                or_(
                    name_lower == normalized,
                    similarity_score >= _ACADEMIC_INTEREST_SIMILARITY_THRESHOLD,
                )
            )
            .order_by(similarity_score.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _resolve_academic_interest_ids(values: list[str | int], db: AsyncSession) -> list[int]:
    from apps.profiles.db_models.academic_interests_db_model import AcademicInterest

    resolved_ids: list[int] = []
    for value in values:
        if value is None:
            continue
        tag_clean = str(value).strip()
        if not tag_clean:
            continue

        if tag_clean.isdigit():
            interest_id = int(tag_clean)
            stmt_interest = select(AcademicInterest).where(AcademicInterest.id == interest_id)
            interest_rec = (await db.execute(stmt_interest)).scalar_one_or_none()
            if interest_rec:
                resolved_ids.append(interest_id)
            continue

        interest_rec = await _find_existing_academic_interest(tag_clean, db)
        if not interest_rec:
            interest_rec = AcademicInterest(
                name=tag_clean,
                education_level_id=1,
                is_active=True,
            )
            db.add(interest_rec)
            await db.flush()
        if interest_rec.id is not None:
            resolved_ids.append(int(interest_rec.id))

    return resolved_ids
