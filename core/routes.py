from fastapi import APIRouter, Depends

from core.security.auth import get_current_user
from apps.accounts.routes import router as accounts_router
from apps.administration.routes import router as admin_router
from apps.profiles.routes import router as profiles_router
from apps.search.routes import router as search_router
from apps.uploads.routes import router as uploads_router


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    router.include_router(accounts_router)
    router.include_router(admin_router)
    router.include_router(profiles_router, dependencies=[Depends(get_current_user)])
    router.include_router(search_router, dependencies=[Depends(get_current_user)])
    router.include_router(uploads_router, dependencies=[Depends(get_current_user)])
    return router
