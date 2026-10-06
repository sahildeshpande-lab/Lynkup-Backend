from __future__ import annotations

from apps.analytics.repositories import fetch_admin_analytics_dashboard
from apps.analytics.schemas import (
    AnalyticsDashboardData,
    AnalyticsDashboardResponse,
    AnalyticsDefaultDistribution,
    AnalyticsDistributionItem,
    AnalyticsDistributionType,
    AnalyticsOverview,
    AnalyticsPeriod,
    AnalyticsTrendItem,
    AnalyticsTypedDistribution,
    LearningSpotlightAnalytics,
    LearningSpotlightCycleStats,
    LearningSpotlightPaperStats,
    LearningSpotlightSummary,
    LearningSpotlightTypeReadSummary,
)
from common.exceptions import ApiError
from common.responses import success_response
from sqlalchemy.ext.asyncio import AsyncSession

def _normalize_days(days: int | None) -> int:
    resolved = 30 if days is None else int(days)
    if resolved <= 0:
        raise ApiError("days must be greater than 0")
    return resolved


def _normalize_type(distribution_type: str | None) -> AnalyticsDistributionType | None:
    if distribution_type is None or distribution_type == "":
        return None
    try:
        return AnalyticsDistributionType(distribution_type)
    except ValueError as exc:
        raise ApiError("type must be one of: university, country, major") from exc


def _map_distribution_items(raw_items: list | None) -> list[AnalyticsDistributionItem]:
    items: list[AnalyticsDistributionItem] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        iso_code = item.get("iso_code")
        if iso_code is None:
            iso_code = item.get("isoCode")
        if isinstance(iso_code, str):
            iso_code = iso_code.strip() or None
        else:
            iso_code = None
        items.append(
            AnalyticsDistributionItem(
                name=str(item.get("name") or ""),
                iso_code=iso_code,
                count=int(item.get("count") or 0),
                percentage=float(item.get("percentage") or 0.0),
            )
        )
    return items


def _map_trend(raw_items: list | None) -> list[AnalyticsTrendItem]:
    items: list[AnalyticsTrendItem] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        items.append(
            AnalyticsTrendItem(
                date=str(item.get("date") or ""),
                count=int(item.get("count") or 0),
            )
        )
    return items


def _map_type_read_summary(raw: object | None) -> LearningSpotlightTypeReadSummary:
    if not isinstance(raw, dict):
        return LearningSpotlightTypeReadSummary()
    return LearningSpotlightTypeReadSummary(total_read=int(raw.get("total_read") or 0))


def _map_learning_spotlight(raw: object | None) -> LearningSpotlightAnalytics:
    if not isinstance(raw, dict):
        return LearningSpotlightAnalytics()

    summary_raw = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    summary = LearningSpotlightSummary(
        leading_thinker=_map_type_read_summary(summary_raw.get("leading_thinker")),
        country_perspective=_map_type_read_summary(
            summary_raw.get("country_perspective")
        ),
        influential_research=_map_type_read_summary(
            summary_raw.get("influential_research")
        ),
        latest_research=_map_type_read_summary(summary_raw.get("latest_research")),
        beyond_your_field=_map_type_read_summary(summary_raw.get("beyond_your_field")),
        total_read=int(summary_raw.get("total_read") or 0),
    )

    cycles: list[LearningSpotlightCycleStats] = []
    for cycle in raw.get("cycles") or []:
        if not isinstance(cycle, dict):
            continue
        papers: list[LearningSpotlightPaperStats] = []
        for paper in cycle.get("papers") or []:
            if not isinstance(paper, dict):
                continue
            paper_id = str(paper.get("paper_id") or "").strip()
            if not paper_id:
                continue
            article_name = paper.get("article_name")
            if article_name is not None:
                article_name = str(article_name)
            papers.append(
                LearningSpotlightPaperStats(
                    rank=int(paper.get("rank") or 0),
                    paper_id=paper_id,
                    article_name=article_name,
                    total_users_recommended=int(
                        paper.get("total_users_recommended") or 0
                    ),
                    total_read_time_seconds=int(
                        paper.get("total_read_time_seconds") or 0
                    ),
                )
            )
        cycles.append(
            LearningSpotlightCycleStats(
                cycle_day=int(cycle.get("cycle_day") or 0),
                spotlight_type=str(cycle.get("spotlight_type") or ""),
                papers=papers,
            )
        )

    return LearningSpotlightAnalytics(summary=summary, cycles=cycles)


def _map_dashboard_payload(
    payload: dict,
    *,
    days: int,
    distribution_type: AnalyticsDistributionType | None,
) -> AnalyticsDashboardData:
    period_raw = payload.get("period") or {}
    overview_raw = payload.get("overview") or {}
    distribution_raw = payload.get("distribution") or {}

    period = AnalyticsPeriod(
        days=int(period_raw.get("days") or days),
        start_date=str(period_raw.get("start_date") or ""),
        end_date=str(period_raw.get("end_date") or ""),
    )
    overview = AnalyticsOverview(
        total_users=int(overview_raw.get("total_users") or 0),
        daily_active_users=int(overview_raw.get("daily_active_users") or 0),
        new_registrations_today=int(overview_raw.get("new_registrations_today") or 0),
        total_posts=int(overview_raw.get("total_posts") or 0),
        learning_spotlight=_map_learning_spotlight(
            overview_raw.get("learning_spotlight")
        ),
    )

    if distribution_type is None:
        distribution: AnalyticsTypedDistribution | AnalyticsDefaultDistribution = (
            AnalyticsDefaultDistribution(
                universities=_map_distribution_items(distribution_raw.get("universities")),
                countries=_map_distribution_items(distribution_raw.get("countries")),
                majors=_map_distribution_items(distribution_raw.get("majors")),
            )
        )
    else:
        distribution = AnalyticsTypedDistribution(
            type=distribution_type,
            items=_map_distribution_items(distribution_raw.get("items")),
        )

    return AnalyticsDashboardData(
        period=period,
        overview=overview,
        registration_trend=_map_trend(payload.get("registration_trend")),
        dau_trend=_map_trend(payload.get("dau_trend")),
        distribution=distribution,
    )


async def get_admin_analytics_dashboard(
    db: AsyncSession,
    *,
    days: int | None = 30,
    distribution_type: str | None = None,
) -> AnalyticsDashboardResponse:
    resolved_days = _normalize_days(days)
    resolved_type = _normalize_type(distribution_type)
    type_value = resolved_type.value if resolved_type is not None else None

    payload = await fetch_admin_analytics_dashboard(
        db,
        days=resolved_days,
        distribution_type=type_value,
    )
    data = _map_dashboard_payload(
        payload,
        days=resolved_days,
        distribution_type=resolved_type,
    )
    return success_response(
        "Analytics dashboard fetched successfully",
        data,
        response_cls=AnalyticsDashboardResponse,
    )
