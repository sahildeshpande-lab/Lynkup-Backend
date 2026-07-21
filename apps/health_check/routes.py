from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import get_session
from . import services
from .schemas import ApiResponse


router = APIRouter(prefix="/health", tags=["Health Check"])


@router.get("/liveness", response_model=ApiResponse)
async def liveness() -> ApiResponse:
    return ApiResponse(message="service is live", data={"service": "ok"})


@router.get("/readiness", response_model=ApiResponse)
async def readiness(db: AsyncSession = Depends(get_session)) -> ApiResponse | JSONResponse:
    try:
        database_ready = await services.check_database(db)
    except Exception:
        database_ready = False

    if not database_ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": False,
                "message": "service is not ready",
                "data": {"database": "unavailable"},
            },
        )

    return ApiResponse(message="service is ready", data={"database": "ok"})
