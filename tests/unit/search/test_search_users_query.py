from __future__ import annotations

from apps.search.services import _search_users_text_clause


def _sql(query: str) -> str:
    clause = _search_users_text_clause(query)
    assert clause is not None
    return str(clause.compile(compile_kwargs={"literal_binds": True})).lower()


def test_cs_matches_major_exactly_not_as_substring() -> None:
    sql = _sql("CS")
    assert "lower(trim(profiles.major)) = 'cs'" in sql
    assert "profiles.major like" not in sql
    assert "profiles.minor" not in sql


def test_computer_science_matches_full_major_form() -> None:
    sql = _sql("Computer Science")
    assert "lower(trim(profiles.major)) = 'computer science'" in sql
    assert "profiles.minor" not in sql


def test_minor_is_not_included_in_user_search() -> None:
    sql = _sql("Math")
    assert "minor" not in sql
