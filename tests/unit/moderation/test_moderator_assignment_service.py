from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlmodel import select

from core.database.session import async_session_factory, engine
from core.database.init import init_db
from apps.accounts.db_models import User
from apps.accounts.services import assign_user_role
from apps.feed.schemas import SavePostRequest, PostContentPayload
from apps.feed.services.post_service import save_post_service
from apps.moderation.services.moderator_assignment_service import pick_next_moderator
from common.enums import PostState, UserStatus
from common.exceptions import ApiError


@pytest_asyncio.fixture
async def db_ready():
    await init_db()
    async with async_session_factory() as session:
        from sqlalchemy import text
        await session.execute(text(
            "DELETE FROM post_revisions WHERE post_id IN "
            "(SELECT id FROM posts WHERE author_user_id IN "
            "(SELECT id FROM users WHERE email LIKE 'pytest_mod_%'))"
        ))
        await session.execute(text(
            "DELETE FROM posts WHERE author_user_id IN "
            "(SELECT id FROM users WHERE email LIKE 'pytest_mod_%')"
        ))
        await session.execute(text("DELETE FROM moderation_assignment_state"))
        await session.execute(text(
            "DELETE FROM user_roles WHERE user_id IN "
            "(SELECT id FROM users WHERE email LIKE 'pytest_mod_%')"
        ))
        await session.execute(text("DELETE FROM users WHERE email LIKE 'pytest_mod_%'"))
        await session.commit()
    yield
    await engine.dispose()


def test_pick_next_moderator_round_robin() -> None:
    mod_a = uuid.uuid4()
    mod_b = uuid.uuid4()
    mod_c = uuid.uuid4()
    moderator_ids = [mod_a, mod_b, mod_c]

    assert pick_next_moderator(moderator_ids, None) == mod_a
    assert pick_next_moderator(moderator_ids, mod_a) == mod_b
    assert pick_next_moderator(moderator_ids, mod_b) == mod_c
    assert pick_next_moderator(moderator_ids, mod_c) == mod_a
    assert pick_next_moderator(moderator_ids, uuid.uuid4()) == mod_a


def test_pick_next_moderator_single_moderator() -> None:
    mod_a = uuid.uuid4()
    assert pick_next_moderator([mod_a], None) == mod_a
    assert pick_next_moderator([mod_a], mod_a) == mod_a


def test_pick_next_moderator_empty_raises() -> None:
    with pytest.raises(ApiError):
        pick_next_moderator([], None)


async def _create_moderator(session, suffix: str) -> User:
    user = User(
        email=f"pytest_mod_assign_{suffix}@example.com",
        firebase_uid=f"uid-mod-{suffix}-{uuid.uuid4()}",
        status=UserStatus.active,
    )
    session.add(user)
    await session.flush()
    await assign_user_role(session, user, "moderator")
    await session.commit()
    await session.refresh(user)
    return user


async def _create_author(session) -> User:
    user = User(
        email=f"pytest_mod_author_{uuid.uuid4()}@example.com",
        firebase_uid=f"uid-author-{uuid.uuid4()}",
        status=UserStatus.active,
    )
    session.add(user)
    await session.flush()
    await assign_user_role(session, user, "user")
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_save_post_assigns_moderators_round_robin(db_ready, monkeypatch) -> None:
    async with async_session_factory() as session:
        mod_a = await _create_moderator(session, "a")
        mod_b = await _create_moderator(session, "b")
        author = await _create_author(session)

    async def _limited_moderators(_db):
        return [mod_a.id, mod_b.id]

    monkeypatch.setattr(
        "apps.moderation.services.moderator_assignment_service._fetch_active_moderator_ids",
        _limited_moderators,
    )

    payload = SavePostRequest(
        content=PostContentPayload(
            caption="Needs review",
            content_html="Hello",
            visibility="public",
        )
    )

    async with async_session_factory() as session:
        author_db = (
            await session.execute(select(User).where(User.id == author.id))
        ).scalar_one()
        post_one = await save_post_service(author_db.id, payload, session)
        assert post_one.state == PostState.processing
        assert post_one.moderator_id == mod_a.id

    async with async_session_factory() as session:
        author_db = (
            await session.execute(select(User).where(User.id == author.id))
        ).scalar_one()
        post_two = await save_post_service(author_db.id, payload, session)
        assert post_two.moderator_id == mod_b.id

    async with async_session_factory() as session:
        author_db = (
            await session.execute(select(User).where(User.id == author.id))
        ).scalar_one()
        post_three = await save_post_service(author_db.id, payload, session)
        assert post_three.moderator_id == mod_a.id


@pytest.mark.asyncio
async def test_save_post_without_moderators_raises(db_ready, monkeypatch) -> None:
    async def _no_moderators(_db):
        return []

    monkeypatch.setattr(
        "apps.moderation.services.moderator_assignment_service._fetch_active_moderator_ids",
        _no_moderators,
    )

    async with async_session_factory() as session:
        author = await _create_author(session)

    payload = SavePostRequest(
        content=PostContentPayload(
            caption="Needs review",
            content_html="Hello",
            visibility="public",
        )
    )

    async with async_session_factory() as session:
        author_db = (
            await session.execute(select(User).where(User.id == author.id))
        ).scalar_one()
        with pytest.raises(ApiError, match="No active moderators"):
            await save_post_service(author_db.id, payload, session)
