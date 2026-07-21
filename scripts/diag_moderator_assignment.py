"""Diagnose moderator round-robin assignment."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, text

from apps.accounts.db_models import Role, User, UserRole
from apps.feed.db_models import Post
from apps.moderation.services.moderator_assignment_service import (
    _fetch_active_moderator_ids,
    assign_next_moderator_round_robin,
)
from common.enums import PostState
from core.database.init import init_db
from core.database.session import async_session_factory


async def main() -> None:
    await init_db()
    async with async_session_factory() as session:
        mods = await _fetch_active_moderator_ids(session)
        print(f"active_moderator_ids ({len(mods)}): {mods}")

        rows = (
            await session.execute(
                select(User.id, User.email, User.status, User.is_deleted, Role.name)
                .join(UserRole, UserRole.user_id == User.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.name.in_(("moderator", "superadmin")))
            )
        ).all()
        print("all moderator/superadmin users:")
        for row in rows:
            print(f"  {row}")

        unassigned = int(
            (
                await session.execute(
                    select(func.count(Post.id)).where(
                        Post.moderator_id.is_(None),
                        Post.state == PostState.published,
                    )
                )
            ).scalar_one()
        )
        print(f"unassigned published posts: {unassigned}")

        table = (
            await session.execute(
                text("SELECT to_regclass('public.moderation_assignment_state')")
            )
        ).scalar_one()
        print(f"moderation_assignment_state table: {table}")

        if mods:
            try:
                next_id = await assign_next_moderator_round_robin(session)
                print(f"round_robin test next moderator: {next_id}")
                await session.rollback()
            except Exception as exc:
                print(f"round_robin test FAILED: {type(exc).__name__}: {exc}")
                await session.rollback()


if __name__ == "__main__":
    asyncio.run(main())
