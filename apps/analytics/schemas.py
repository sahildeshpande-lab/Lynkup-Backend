from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from common.schemas import ApiResponse


class AnalyticsDistributionType(str, Enum):
    university = "university"
    country = "country"
    major = "major"


class AnalyticsPeriod(BaseModel):
    days: int
    start_date: str
    end_date: str


class LearningSpotlightTypeReadSummary(BaseModel):
    total_read: int = 0


class LearningSpotlightSummary(BaseModel):
    leading_thinker: LearningSpotlightTypeReadSummary = Field(
        default_factory=LearningSpotlightTypeReadSummary
    )
    country_perspective: LearningSpotlightTypeReadSummary = Field(
        default_factory=LearningSpotlightTypeReadSummary
    )
    influential_research: LearningSpotlightTypeReadSummary = Field(
        default_factory=LearningSpotlightTypeReadSummary
    )
    latest_research: LearningSpotlightTypeReadSummary = Field(
        default_factory=LearningSpotlightTypeReadSummary
    )
    beyond_your_field: LearningSpotlightTypeReadSummary = Field(
        default_factory=LearningSpotlightTypeReadSummary
    )
    total_read: int = 0


class LearningSpotlightPaperStats(BaseModel):
    rank: int
    paper_id: str
    article_name: str | None = None
    total_users_recommended: int = 0
    total_read_time_seconds: int = 0


class LearningSpotlightCycleStats(BaseModel):
    cycle_day: int
    spotlight_type: str
    papers: list[LearningSpotlightPaperStats] = Field(default_factory=list)


class LearningSpotlightAnalytics(BaseModel):
    summary: LearningSpotlightSummary = Field(default_factory=LearningSpotlightSummary)
    cycles: list[LearningSpotlightCycleStats] = Field(default_factory=list)


class AnalyticsOverview(BaseModel):
    total_users: int = 0
    daily_active_users: int = 0
    new_registrations_today: int = 0
    total_posts: int = 0
    learning_spotlight: LearningSpotlightAnalytics = Field(
        default_factory=LearningSpotlightAnalytics
    )


class AnalyticsTrendItem(BaseModel):
    date: str
    count: int = 0


class AnalyticsDistributionItem(BaseModel):
    name: str
    iso_code: str | None = None
    count: int = 0
    percentage: float = 0.0


class AnalyticsTypedDistribution(BaseModel):
    type: AnalyticsDistributionType
    items: list[AnalyticsDistributionItem] = Field(default_factory=list)


class AnalyticsDefaultDistribution(BaseModel):
    universities: list[AnalyticsDistributionItem] = Field(default_factory=list)
    countries: list[AnalyticsDistributionItem] = Field(default_factory=list)
    majors: list[AnalyticsDistributionItem] = Field(default_factory=list)


class AnalyticsDashboardData(BaseModel):
    period: AnalyticsPeriod
    overview: AnalyticsOverview
    registration_trend: list[AnalyticsTrendItem] = Field(default_factory=list)
    dau_trend: list[AnalyticsTrendItem] = Field(default_factory=list)
    distribution: AnalyticsTypedDistribution | AnalyticsDefaultDistribution


class AnalyticsDashboardResponse(ApiResponse):
    data: AnalyticsDashboardData | None = None
