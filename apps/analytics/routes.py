from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.analytics.schemas import AnalyticsDashboardResponse, AnalyticsDistributionType
from apps.analytics.services import get_admin_analytics_dashboard
from apps.administration.dependencies import require_signed_admin
from core.database.session import get_session

router = APIRouter(tags=["Admin Analytics"])


@router.get(
    "/admin/analytics/dashboard",
    response_model=AnalyticsDashboardResponse,
)
async def admin_analytics_dashboard(
    days: int = Query(
        default=30,
        description="Trend window in days (any positive integer; FE typically sends 7, 30, or 90)",
    ),
    type: AnalyticsDistributionType | None = Query(
        default=None,
        description="Optional demographic distribution: university, country, or major",
    ),
    db: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_signed_admin),
) -> AnalyticsDashboardResponse:
    _ = current_user
    return await get_admin_analytics_dashboard(
        db,
        days=days,
        distribution_type=type.value if type is not None else None,
    )
