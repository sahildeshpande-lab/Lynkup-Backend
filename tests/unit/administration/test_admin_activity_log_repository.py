from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.administration.repositories.admin_activity_log_repository import (
    _activity_logs_order_by,
    _staff_display_name,
    count_unread_admin_notification_activity_logs,
    list_admin_activity_logs,
    list_admin_notification_activity_logs,
)
from common.enums import AdminActivityLogOrder, AdminActivityLogSort


def _scalar_result(rows, count):
    result = MagicMock()
    result.scalar_one.return_value = count
    result.all.return_value = rows
    return result


def _compile_sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect())).lower()


def _compile_order_sql(sort=None, order=None) -> str:
    from sqlalchemy.dialects import postgresql

    return " ".join(
        str(clause.compile(dialect=postgresql.dialect())).lower()
        for clause in _activity_logs_order_by(sort, order)
    )


@pytest.mark.asyncio
async def test_list_filters_by_search_and_orders_desc():
    db = AsyncMock()
    log = MagicMock()
    rows = [(log, "Ada", "Lovelace", "ada@example.com")]
    count_result = _scalar_result([], 1)
    list_result = _scalar_result(rows, 1)
    db.execute = AsyncMock(side_effect=[count_result, list_result])

    found, total = await list_admin_activity_logs(
        db,
        search="Rejected",
        offset=0,
        limit=20,
    )

    assert found == [(log, "Ada Lovelace")]
    assert total == 1
    count_stmt = db.execute.await_args_list[0].args[0]
    list_stmt = db.execute.await_args_list[1].args[0]
    count_sql = _compile_sql(count_stmt)
    list_sql = _compile_sql(list_stmt)
    assert "ilike" in count_sql
    assert "description" in count_sql
    assert "concat_ws" in count_sql
    assert "first_name" in count_sql
    assert "last_name" in count_sql
    assert "email" in count_sql
    assert "coalesce" in count_sql
    assert "action" not in count_sql
    assert "not in" in count_sql
    assert "metadata" not in count_sql
    assert "created_at" in list_sql
    assert "desc" in list_sql
    assert "profiles" in list_sql
    assert "users" in list_sql


@pytest.mark.asyncio
async def test_list_filters_by_module_case_insensitive():
    db = AsyncMock()
    count_result = _scalar_result([], 1)
    log = MagicMock()
    list_result = _scalar_result([(log, "Mod", "User", "mod@example.com")], 1)
    db.execute = AsyncMock(side_effect=[count_result, list_result])

    found, total = await list_admin_activity_logs(db, module="  Post ")

    assert total == 1
    assert found == [(log, "Mod User")]
    count_sql = _compile_sql(db.execute.await_args_list[0].args[0])
    assert "lower" in count_sql
    assert "module" in count_sql


@pytest.mark.asyncio
async def test_list_filters_by_role_case_insensitive():
    db = AsyncMock()
    count_result = _scalar_result([], 1)
    log = MagicMock()
    list_result = _scalar_result([(log, "Sam", "Admin", "sam@example.com")], 1)
    db.execute = AsyncMock(side_effect=[count_result, list_result])

    found, total = await list_admin_activity_logs(db, role=" SuperAdmin ", module="user")

    assert total == 1
    assert found == [(log, "Sam Admin")]
    count_sql = _compile_sql(db.execute.await_args_list[0].args[0])
    assert "role" in count_sql
    assert "module" in count_sql


def test_activity_logs_order_by_created_at_is_default_desc():
    sql = _compile_order_sql()
    assert "created_at" in sql
    assert "desc" in sql
    assert "module" not in sql


def test_activity_logs_order_by_created_at_asc():
    sql = _compile_order_sql(AdminActivityLogSort.created_at, AdminActivityLogOrder.asc)
    assert "created_at" in sql
    assert "asc" in sql
    assert "desc" not in sql


def test_activity_logs_order_by_module_asc_then_created_at():
    sql = _compile_order_sql(AdminActivityLogSort.module, AdminActivityLogOrder.asc)
    assert "module" in sql
    assert "created_at" in sql
    assert "asc" in sql


def test_activity_logs_order_by_action_desc():
    sql = _compile_order_sql(AdminActivityLogSort.action, AdminActivityLogOrder.desc)
    assert "action" in sql
    assert "desc" in sql


def test_activity_logs_order_by_updated_at_asc():
    sql = _compile_order_sql(AdminActivityLogSort.updated_at, AdminActivityLogOrder.asc)
    assert "updated_at" in sql
    assert "asc" in sql


def test_staff_display_name_prefers_profile_then_email():
    assert _staff_display_name("Ada", "Lovelace", "ada@example.com") == "Ada Lovelace"
    assert _staff_display_name("Ada", None, "ada@example.com") == "Ada"
    assert _staff_display_name("  ", "", "ada@example.com") == "ada@example.com"
    assert _staff_display_name(None, None, None) is None


@pytest.mark.asyncio
async def test_list_filters_by_moderator_id_and_superadmin_actions():
    from uuid import uuid4
    db = AsyncMock()
    count_result = _scalar_result([], 1)
    log = MagicMock()
    list_result = _scalar_result([(log, "Mod", "User", "mod@example.com")], 1)
    db.execute = AsyncMock(side_effect=[count_result, list_result])

    mod_id = uuid4()
    found, total = await list_admin_activity_logs(db, moderator_id=mod_id)

    assert total == 1
    count_stmt = db.execute.await_args_list[0].args[0]
    count_sql = _compile_sql(count_stmt)
    from sqlalchemy.dialects import postgresql

    compiled = count_stmt.compile(dialect=postgresql.dialect())
    param_values = " ".join(str(value).lower() for value in compiled.params.values())

    assert "user_id" in count_sql
    assert "superadmin" in param_values
    assert "posts" in count_sql
    assert "reports" in count_sql
    assert "moderation_history" in count_sql
    assert "moderator_id" in count_sql


@pytest.mark.asyncio
async def test_list_activity_logs_excludes_hidden_profanity_words_module():
    from sqlalchemy.dialects import postgresql

    db = AsyncMock()
    count_result = _scalar_result([], 0)
    list_result = _scalar_result([], 0)
    db.execute = AsyncMock(side_effect=[count_result, list_result])

    await list_admin_activity_logs(db, module="profanity_words")

    count_stmt = db.execute.await_args_list[0].args[0]
    count_sql = _compile_sql(count_stmt)
    param_values = " ".join(
        str(value).lower() for value in count_stmt.compile(dialect=postgresql.dialect()).params.values()
    )
    assert "not in" in count_sql
    assert "profanity_words" in param_values


@pytest.mark.asyncio
async def test_list_admin_notifications_excludes_hidden_profanity_words_module():
    from sqlalchemy.dialects import postgresql

    db = AsyncMock()
    count_result = _scalar_result([], 0)
    list_result = _scalar_result([], 0)
    db.execute = AsyncMock(side_effect=[count_result, list_result])

    await list_admin_notification_activity_logs(db)

    count_stmt = db.execute.await_args_list[0].args[0]
    count_sql = _compile_sql(count_stmt)
    param_values = " ".join(
        str(value).lower() for value in count_stmt.compile(dialect=postgresql.dialect()).params.values()
    )
    assert "not in" in count_sql
    assert "profanity_words" in param_values


@pytest.mark.asyncio
async def test_count_unread_notifications_excludes_hidden_profanity_words_module():
    from sqlalchemy.dialects import postgresql

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([], 0))

    await count_unread_admin_notification_activity_logs(db)

    count_stmt = db.execute.await_args_list[0].args[0]
    count_sql = _compile_sql(count_stmt)
    param_values = " ".join(
        str(value).lower() for value in count_stmt.compile(dialect=postgresql.dialect()).params.values()
    )
    assert "not in" in count_sql
    assert "profanity_words" in param_values

