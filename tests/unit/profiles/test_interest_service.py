from __future__ import annotations

import uuid

import pytest
from sqlmodel import select

from apps.profiles.db_models.academic_interests_db_model import AcademicInterest
from apps.profiles.services.interest_service import _resolve_academic_interest_ids
from core.database.init import init_db
from core.database.session import async_session_factory, engine


@pytest.mark.asyncio
async def test_resolve_academic_interest_ids_by_name_and_id() -> None:
    try:
        await init_db()
        unique = uuid.uuid4().hex[:8]

        async with async_session_factory() as session:
            existing = AcademicInterest(name=f"Physics_{unique}", is_active=True)
            session.add(existing)
            await session.commit()
            await session.refresh(existing)

            resolved = await _resolve_academic_interest_ids(
                [str(existing.id), f"Math_{unique}", "", None, "  "],
                session,
            )
            await session.commit()

            assert existing.id in resolved
            math = (
                await session.execute(
                    select(AcademicInterest).where(AcademicInterest.name.ilike(f"Math_{unique}"))
                )
            ).scalar_one()
            assert math.id in resolved
    finally:
        await engine.dispose()
