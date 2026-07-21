from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def check_database(db: AsyncSession) -> bool:
    result = await db.execute(text("SELECT 1"))
    return result.scalar_one() == 1
