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


async def _clean_pytest_mod_data(session):
    """Remove leftover pytest_mod_% users and all FK dependents.

    Keep this aligned with feed/connections cleanups so CI does not fail
    one foreign-key constraint at a time.
    """
    from sqlalchemy import text

    mod_users = "SELECT id FROM users WHERE email LIKE 'pytest_mod_%'"
    mod_posts = (
        "SELECT id FROM posts WHERE author_user_id IN "
        f"({mod_users}) OR moderator_id IN ({mod_users})"
    )

    await session.execute(text(
        f"DELETE FROM post_attachments WHERE post_id IN ({mod_posts})"
    ))
    await session.execute(text(
        f"DELETE FROM post_reactions WHERE post_id IN ({mod_posts}) "
        f"OR user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM post_revisions WHERE post_id IN ({mod_posts}) "
        f"OR editor_user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM post_hashtags WHERE post_id IN ({mod_posts})"
    ))
    await session.execute(text(
        f"DELETE FROM post_topics WHERE post_id IN ({mod_posts})"
    ))
    await session.execute(text(
        f"DELETE FROM link_previews WHERE post_id IN ({mod_posts})"
    ))
    await session.execute(text(
        f"DELETE FROM posts WHERE author_user_id IN ({mod_users}) "
        f"OR moderator_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM media_assets WHERE owner_user_id IN ({mod_users})"
    ))
    await session.execute(text("DELETE FROM moderation_assignment_state"))
    await session.execute(text(
        f"DELETE FROM moderation_history WHERE moderator_id IN ({mod_users}) "
        f"OR entity_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM reports WHERE reported_id IN ({mod_users}) "
        f"OR moderator_id IN ({mod_users}) OR entity_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM notifications WHERE recipient_user_id IN ({mod_users})"
    ))
    await session.execute(text(
        "DELETE FROM notification_campaign_audience WHERE user_id IN "
        f"({mod_users})"
    ))
    await session.execute(text(
        "DELETE FROM notification_preferences WHERE user_id IN "
        f"({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM profiles WHERE user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM user_roles WHERE user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM security_events WHERE user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM consent_records WHERE user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM refresh_tokens WHERE user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM password_reset_tokens WHERE user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM user_installations WHERE user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM user_activity_logs WHERE user_id IN ({mod_users})"
    ))
    await session.execute(text(
        f"DELETE FROM users WHERE id IN ({mod_users})"
    ))
    await session.commit()


@pytest_asyncio.fixture
async def db_ready():
    await init_db()
    async with async_session_factory() as session:
        await _clean_pytest_mod_data(session)
    yield
    async with async_session_factory() as session:
        await _clean_pytest_mod_data(session)
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
        assert post_one.state == PostState.published
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
async def test_save_post_without_moderators_soft_fails(db_ready, monkeypatch) -> None:
    async def _no_moderators(_db):
        return []

    monkeypatch.setattr(
        "apps.moderation.services.moderator_assignment_service._fetch_active_moderator_ids",
        _no_moderators,
    )
    monkeypatch.setattr(
        "apps.feed.services.post_service._fetch_active_moderator_ids",
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
        # Soft-fail: post is still created/published when no moderators are available.
        post = await save_post_service(author_db.id, payload, session)
        assert post.state == PostState.published
        assert post.moderator_id is None
