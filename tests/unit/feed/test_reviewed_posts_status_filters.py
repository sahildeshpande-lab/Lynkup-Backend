from __future__ import annotations

from apps.feed.services import post_service as _ps  # noqa: F401 — load package graph first
from apps.feed.repositories.post_repository import (
    _reviewed_posts_order_by,
    _reviewed_posts_search_clause,
    _reviewed_states_for_status,
    resolve_reviewed_posts_sort,
)
from apps.profiles.db_models import Profile
from common.enums import PostState, ReviewedPostSort


def test_reviewed_states_published_includes_reinstate() -> None:
    assert _reviewed_states_for_status("published") == (
        PostState.published,
        PostState.reinstate,
    )
    assert _reviewed_states_for_status(None) == (
        PostState.published,
        PostState.reinstate,
    )


def test_reviewed_states_reinstate_only_when_requested() -> None:
    assert _reviewed_states_for_status("reinstate") == (PostState.reinstate,)


def test_reviewed_states_flagged_includes_processing() -> None:
    assert _reviewed_states_for_status("flagged") == (
        PostState.flagged,
        PostState.processing,
    )


def test_reviewed_states_processing_only_when_requested() -> None:
    assert _reviewed_states_for_status("processing") == (PostState.processing,)


def test_reviewed_states_rejected_only_when_requested() -> None:
    assert _reviewed_states_for_status("rejected") == (PostState.rejected,)


def test_reviewed_posts_search_clause_is_none_for_blank() -> None:
    assert _reviewed_posts_search_clause(None, Profile) is None
    assert _reviewed_posts_search_clause("  ", Profile) is None


def test_reviewed_posts_search_clause_matches_name_and_content() -> None:
    from sqlalchemy.dialects import postgresql

    clause = _reviewed_posts_search_clause("jane", Profile)
    sql = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "ilike" in sql
    assert "%jane%" in sql
    assert "first_name" in sql
    assert "last_name" in sql
    assert "caption" in sql
    assert "content_html" in sql


def _compile_order_sql(sort, order=None) -> str:
    from sqlalchemy.dialects import postgresql

    return " ".join(
        str(clause.compile(dialect=postgresql.dialect())).lower()
        for clause in _reviewed_posts_order_by(sort, order)
    )


def test_reviewed_posts_order_by_created_at_is_default() -> None:
    for sort in (None, ReviewedPostSort.created_at):
        sql = _compile_order_sql(sort)
        assert "created_at" in sql
        assert "updated_at" not in sql
        assert "desc" in sql


def test_reviewed_posts_order_by_updated_at() -> None:
    from common.enums import ReviewedPostOrder

    for sort in (ReviewedPostSort.updated_at, ReviewedPostSort.latest_post):
        sql = _compile_order_sql(sort)
        assert "updated_at" in sql
        assert "created_at" not in sql
        assert "desc" in sql

    sql_asc = _compile_order_sql(ReviewedPostSort.updated_at, ReviewedPostOrder.asc)
    assert "updated_at" in sql_asc
    assert "asc" in sql_asc


def test_reviewed_posts_order_by_created_at_asc() -> None:
    from common.enums import ReviewedPostOrder

    sql = _compile_order_sql(ReviewedPostSort.created_at, ReviewedPostOrder.asc)
    assert "created_at" in sql
    assert "asc" in sql


def test_resolve_reviewed_posts_sort_defaults() -> None:
    assert resolve_reviewed_posts_sort(None, "published") == ReviewedPostSort.created_at
    assert resolve_reviewed_posts_sort(None, "rejected") == ReviewedPostSort.created_at
    assert resolve_reviewed_posts_sort(None, None) == ReviewedPostSort.created_at
    assert resolve_reviewed_posts_sort(None, "flagged") == ReviewedPostSort.updated_at
    assert (
        resolve_reviewed_posts_sort(ReviewedPostSort.created_at, "flagged")
        == ReviewedPostSort.created_at
    )

