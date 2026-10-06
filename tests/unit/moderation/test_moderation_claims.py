from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.dml import Update

from core.jobs.claims import claim_moderation_comment, claim_moderation_post


def _compile(stmt) -> str:
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()


def _statement(db):
    return db.execute.await_args.args[0]


@pytest.fixture
def post_id():
    return uuid4()


@pytest.fixture
def comment_id():
    return uuid4()


@pytest.mark.asyncio
async def test_post_can_be_atomically_claimed(mock_db, post_id):
    db = mock_db(SimpleNamespace(rowcount=1))

    result = await claim_moderation_post(
        db,
        post_id=post_id,
        lease_owner="worker-1",
        lease_seconds=300,
    )

    assert result.claimed is True
    assert result.entity_id == post_id
    assert db.execute.await_count == 1
    assert db.commit.await_count == 1
    stmt = _statement(db)
    assert isinstance(stmt, Update)
    sql = _compile(stmt)
    assert "update posts" in sql
    assert "moderation_lease_owner" in sql
    assert "moderation_lease_expires_at" in sql
    assert "moderation_attempt_count" in sql
    assert "auto_moderation_scanned_at" in sql
    assert "current_timestamp" in sql
    assert "interval '300 seconds'" in sql
    assert "returning" in sql
    assert "+ 1" in sql or "+1" in sql
    assert "select " not in sql.split("update", 1)[0]


@pytest.mark.asyncio
async def test_comment_can_be_atomically_claimed(mock_db, comment_id):
    db = mock_db(SimpleNamespace(rowcount=1))

    result = await claim_moderation_comment(
        db,
        comment_id=comment_id,
        lease_owner="worker-1",
        lease_seconds=300,
    )

    assert result.claimed is True
    assert result.entity_id == comment_id
    assert db.commit.await_count == 1
    sql = _compile(_statement(db))
    assert "update comments" in sql
    assert "is_deleted" in sql
    assert "moderation_lease_owner" in sql
    assert "moderation_attempt_count" in sql
    assert "auto_moderation_scanned_at" in sql
    assert "current_timestamp" in sql
    assert "returning" in sql


@pytest.mark.asyncio
async def test_two_concurrent_post_claims_only_one_succeeds(mock_db, post_id):
    db = mock_db(SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=0))

    results = await asyncio.gather(
        claim_moderation_post(db, post_id=post_id, lease_owner="worker-a"),
        claim_moderation_post(db, post_id=post_id, lease_owner="worker-b"),
    )

    assert [item.claimed for item in results].count(True) == 1
    assert [item.claimed for item in results].count(False) == 1
    assert db.execute.await_count == 2
    assert db.commit.await_count == 1
    for call in db.execute.await_args_list:
        assert isinstance(call.args[0], Update)


@pytest.mark.asyncio
async def test_two_concurrent_comment_claims_only_one_succeeds(mock_db, comment_id):
    db = mock_db(SimpleNamespace(rowcount=1), SimpleNamespace(rowcount=0))

    results = await asyncio.gather(
        claim_moderation_comment(db, comment_id=comment_id, lease_owner="worker-a"),
        claim_moderation_comment(db, comment_id=comment_id, lease_owner="worker-b"),
    )

    assert [item.claimed for item in results].count(True) == 1
    assert [item.claimed for item in results].count(False) == 1
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_active_lease_prevents_second_post_claim(mock_db, post_id):
    db = mock_db(SimpleNamespace(rowcount=0))

    result = await claim_moderation_post(
        db,
        post_id=post_id,
        lease_owner="worker-2",
    )

    assert result.claimed is False
    assert db.commit.await_count == 0
    sql = _compile(_statement(db))
    where_sql = sql.split("where", 1)[-1]
    assert "moderation_lease_owner" in where_sql
    assert "moderation_lease_expires_at" in where_sql
    assert "current_timestamp" in where_sql


@pytest.mark.asyncio
async def test_expired_post_lease_can_be_reclaimed(mock_db, post_id):
    db = mock_db(SimpleNamespace(rowcount=1))

    result = await claim_moderation_post(
        db,
        post_id=post_id,
        lease_owner="worker-3",
        lease_seconds=120,
    )

    assert result.claimed is True
    sql = _compile(_statement(db))
    assert "moderation_lease_expires_at" in sql
    assert "current_timestamp" in sql
    assert "interval '120 seconds'" in sql
    assert "moderation_attempt_count" in sql


@pytest.mark.asyncio
async def test_already_scanned_post_is_not_claimed(mock_db, post_id):
    db = mock_db(SimpleNamespace(rowcount=0))

    result = await claim_moderation_post(
        db,
        post_id=post_id,
        lease_owner="worker-4",
    )

    assert result.claimed is False
    assert db.commit.await_count == 0
    sql = _compile(_statement(db))
    where_sql = sql.split("where", 1)[-1]
    assert "auto_moderation_scanned_at" in where_sql
    assert "updated_at" in where_sql


@pytest.mark.asyncio
async def test_deleted_comment_is_not_claimed(mock_db, comment_id):
    db = mock_db(SimpleNamespace(rowcount=0))

    result = await claim_moderation_comment(
        db,
        comment_id=comment_id,
        lease_owner="worker-4",
    )

    assert result.claimed is False
    assert db.commit.await_count == 0
    sql = _compile(_statement(db))
    where_sql = sql.split("where", 1)[-1]
    assert "is_deleted" in where_sql
    assert "false" in where_sql


@pytest.mark.asyncio
async def test_skipped_post_states_are_not_claimed(mock_db, post_id):
    db = mock_db(SimpleNamespace(rowcount=0))

    result = await claim_moderation_post(
        db,
        post_id=post_id,
        lease_owner="worker-5",
    )

    assert result.claimed is False
    sql = _compile(_statement(db))
    where_sql = sql.split("where", 1)[-1]
    assert "draft" in where_sql
    assert "deleted" in where_sql
    assert "rejected" in where_sql


def _postgres_async_url() -> str | None:
    from core.database.config import settings

    url = settings.async_database_url
    if url.startswith("postgresql"):
        return url
    return None


@pytest.mark.asyncio
async def test_postgres_concurrent_claims_expired_lease_and_ineligible_rows():
    pg_url = _postgres_async_url()
    if pg_url is None:
        pytest.skip("PostgreSQL not available")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from apps.accounts.db_models import User
    from apps.engagement.db_models import Comment
    from apps.feed.db_models import Post
    from common.enums import PostState

    engine = create_async_engine(pg_url, poolclass=NullPool)
    missing_lease_columns = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            post_col = await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'posts'
                      AND column_name = 'moderation_attempt_count'
                    """
                )
            )
            comment_col = await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'comments'
                      AND column_name = 'moderation_attempt_count'
                    """
                )
            )
            await conn.commit()
            missing_lease_columns = post_col.first() is None or comment_col.first() is None
    except Exception:
        await engine.dispose()
        pytest.skip("PostgreSQL not available")

    if missing_lease_columns:
        await engine.dispose()
        pytest.skip("moderation lease columns are not present")

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    user = User(email=f"mod-claim-{uuid4().hex[:10]}@example.test")
    post = None
    scanned_post = None
    skipped_post = None
    comment = None
    deleted_comment = None
    try:
        async with factory() as session:
            session.add(user)
            await session.flush()
            post = Post(
                author_user_id=user.id,
                state=PostState.published,
                content={"caption": "claim me"},
            )
            scanned_post = Post(
                author_user_id=user.id,
                state=PostState.published,
                content={"caption": "already scanned"},
                auto_moderation_scanned_at=now,
                updated_at=now - timedelta(minutes=1),
            )
            skipped_post = Post(
                author_user_id=user.id,
                state=PostState.draft,
                content={"caption": "draft"},
            )
            session.add(post)
            session.add(scanned_post)
            session.add(skipped_post)
            await session.flush()
            comment = Comment(
                post_id=post.id,
                user_id=user.id,
                comment_text="claim me too",
            )
            deleted_comment = Comment(
                post_id=post.id,
                user_id=user.id,
                comment_text="deleted",
                is_deleted=True,
            )
            session.add(comment)
            session.add(deleted_comment)
            await session.commit()
            await session.refresh(post)
            await session.refresh(scanned_post)
            await session.refresh(skipped_post)
            await session.refresh(comment)
            await session.refresh(deleted_comment)

        async with factory() as s1, factory() as s2:
            post_results = await asyncio.gather(
                claim_moderation_post(s1, post_id=post.id, lease_owner="worker-a"),
                claim_moderation_post(s2, post_id=post.id, lease_owner="worker-b"),
            )
        assert [item.claimed for item in post_results].count(True) == 1
        assert [item.claimed for item in post_results].count(False) == 1

        async with factory() as s1, factory() as s2:
            comment_results = await asyncio.gather(
                claim_moderation_comment(s1, comment_id=comment.id, lease_owner="worker-a"),
                claim_moderation_comment(s2, comment_id=comment.id, lease_owner="worker-b"),
            )
        assert [item.claimed for item in comment_results].count(True) == 1
        assert [item.claimed for item in comment_results].count(False) == 1

        async with factory() as session:
            already_scanned = await claim_moderation_post(
                session,
                post_id=scanned_post.id,
                lease_owner="worker-scan",
            )
            skipped = await claim_moderation_post(
                session,
                post_id=skipped_post.id,
                lease_owner="worker-skip",
            )
            deleted = await claim_moderation_comment(
                session,
                comment_id=deleted_comment.id,
                lease_owner="worker-deleted",
            )
        assert already_scanned.claimed is False
        assert skipped.claimed is False
        assert deleted.claimed is False

        async with factory() as session:
            stored_post = await session.get(Post, post.id)
            stored_comment = await session.get(Comment, comment.id)
            assert stored_post is not None
            assert stored_comment is not None
            stored_post.moderation_lease_expires_at = now - timedelta(minutes=5)
            stored_comment.moderation_lease_expires_at = now - timedelta(minutes=5)
            session.add(stored_post)
            session.add(stored_comment)
            await session.commit()

        async with factory() as session:
            reclaimed_post = await claim_moderation_post(
                session,
                post_id=post.id,
                lease_owner="worker-new",
            )
            reclaimed_comment = await claim_moderation_comment(
                session,
                comment_id=comment.id,
                lease_owner="worker-new",
            )
        assert reclaimed_post.claimed is True
        assert reclaimed_comment.claimed is True
    finally:
        async with factory() as session:
            for row in (deleted_comment, comment, scanned_post, skipped_post, post, user):
                if row is None:
                    continue
                stored = await session.get(type(row), row.id)
                if stored is not None:
                    await session.delete(stored)
            await session.commit()
        await engine.dispose()
