from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from apps.report.db_models import Report
from apps.report.repositories.report_repository import _reported_entities_order_by
from common.enums import ReportedEntityOrder, ReportedEntitySort


def _ranked():
    return (
        select(
            Report.entity_id.label("entity_id"),
            func.count(Report.id).label("report_count"),
            Report.created_at.label("latest_reported_at"),
            Report.created_at.label("created_at"),
            Report.updated_at.label("updated_at"),
        ).subquery()
    )


def _compile_order_sql(sort, order=None) -> str:
    return " ".join(
        str(clause.compile(dialect=postgresql.dialect())).lower()
        for clause in _reported_entities_order_by(_ranked(), sort, order)
    )


def test_reported_entities_order_by_report_count_is_default() -> None:
    for sort in (None, ReportedEntitySort.report_count):
        sql = _compile_order_sql(sort)
        assert "report_count" in sql
        assert "latest_reported_at" in sql
        assert "desc" in sql


def test_reported_entities_order_by_created_at() -> None:
    sql = _compile_order_sql(ReportedEntitySort.created_at)
    assert "created_at" in sql
    assert "latest_reported_at" not in sql
    assert "report_count" not in sql
    assert "desc" in sql

    sql_asc = _compile_order_sql(ReportedEntitySort.created_at, ReportedEntityOrder.asc)
    assert "created_at" in sql_asc
    assert "asc" in sql_asc


def test_reported_entities_order_by_updated_at() -> None:
    sql = _compile_order_sql(ReportedEntitySort.updated_at)
    assert "updated_at" in sql
    assert "report_count" not in sql
    assert "desc" in sql

    sql_asc = _compile_order_sql(ReportedEntitySort.updated_at, ReportedEntityOrder.asc)
    assert "updated_at" in sql_asc
    assert "asc" in sql_asc


def test_reported_entities_order_by_latest_reported_at() -> None:
    sql = _compile_order_sql(ReportedEntitySort.latest_reported_at)
    assert "latest_reported_at" in sql
    assert "report_count" not in sql
    assert "desc" in sql
