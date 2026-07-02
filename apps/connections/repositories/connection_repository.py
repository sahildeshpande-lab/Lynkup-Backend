from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.connections.db_models import Connection


def _connection_pair(user_id_1: UUID, user_id_2: UUID) -> tuple[UUID, UUID]:
    return (user_id_1, user_id_2) if user_id_1 < user_id_2 else (user_id_2, user_id_1)


async def get_active_connection_between(
    db: AsyncSession,
    user_id_1: UUID,
    user_id_2: UUID,
) -> Connection | None:
    """Return the active accepted connection between two users, if one exists."""
    low_id, high_id = _connection_pair(user_id_1, user_id_2)
    result = await db.execute(
        select(Connection).where(
            Connection.user_low_id == low_id,
            Connection.user_high_id == high_id,
            Connection.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def delete_connection(db: AsyncSession, connection: Connection) -> None:
    """Delete an accepted connection row."""
    await db.delete(connection)
