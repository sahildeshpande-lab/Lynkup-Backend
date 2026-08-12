from fastapi import APIRouter

from apps.accounts.routes import router as accounts_router
from apps.administration.routes import router as admin_router
from apps.health_check.routes import router as health_check_router
from apps.profiles.routes import router as profiles_router
from apps.search.routes import router as search_router
from apps.uploads.routes import router as uploads_router
from apps.connections.routes import router as connections_router
from apps.feed.routes import router as feed_router
from apps.engagement.routes import router as engagement_router
from apps.moderation.routes import router as moderation_router
from apps.invitations.routes import router as invitations_router
from apps.report.routes import router as report_router
from apps.chat.router import router as chat_router
from apps.notifications.routes import router as notifications_router
from apps.recommendations.routes import router as recommendation_router
from apps.export.router import router as export_router
from apps.threshold_configuration.routes import router as threshold_configuration_router
from apps.bulk_send.router import router as bulk_send_router
from apps.share.router import router as share_router


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    router.include_router(health_check_router)
    router.include_router(accounts_router)
    router.include_router(admin_router)
    router.include_router(moderation_router)
    router.include_router(profiles_router)
    router.include_router(search_router)
    router.include_router(uploads_router)
    router.include_router(connections_router)
    router.include_router(engagement_router)
    router.include_router(threshold_configuration_router)
    router.include_router(report_router)
    router.include_router(feed_router)
    router.include_router(invitations_router)
    router.include_router(chat_router)
    router.include_router(notifications_router)
    # router.include_router(recommendation_router)
    router.include_router(export_router)
    router.include_router(bulk_send_router)
    router.include_router(share_router)
    return router
