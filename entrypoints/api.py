from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI,HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from core.lifespan import lifespan
from core.routes import build_router
from core.security.auth import bearer_scheme
from common.responses import error_response
from apps.accounts.services import AccountExistsException


AUTH_TAG = "1] User Registration, Authentication & Onboarding"
USER_TAG = "2] User Management"
DISCOVERY_TAG = "3] Search & Discovery"
ADMIN_TAG = "4] Admin Management"
CONNECTION_TAG = "5] Connection Managements" 



app = FastAPI(
    title="KampuLynk User Management API",
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": AUTH_TAG,
            "description": "Signup, verification, social login, session refresh, and logout.",
        },
        {
            "name": USER_TAG,
            "description": "Authenticated user profile, password, export, delete, and public profile APIs.",
        },
        {
            "name": DISCOVERY_TAG,
            "description": "Search & discovery APIs (search by name/keyword/hashtag, filter by university/interest, etc.).",
        },
        {
            "name": ADMIN_TAG,
            "description": "Admin-only authentication and user management APIs.",
        },
        {
            "name": CONNECTION_TAG,
            "description": "API for Lynkup , follow and Block user.",
        },
    ],
)

import time 
import logging

logger=logging.getLogger(__name__)
# @app.middleware("http")
# async def log_requests(request,call_next):
#     start=time.time()
#     logger.info(f"START{request.method} {request.url}")
#     response=await call_next(request)
#     logger.info(
#         f"END {request.method} {request.url.path}"
#         f"Status = {response.status_code}"
#         f"Time ={time.time()-start:.2f}s"

#     )
#     return response

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=200,
        content=error_response(str(exc.detail), data={"httpStatus": exc.status_code}).model_dump(),
    )


@app.exception_handler(AccountExistsException)
async def account_exists_exception_handler(request, exc):
    return JSONResponse(
        status_code=200,
        content=error_response(
            "Account already exists. Please login using your registered method.",
            data={
                "registration_type": exc.registration_type,
                "httpStatus": 409,
            },
        ).model_dump(),
    )

def make_json_serializable(obj):
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_serializable(item) for item in obj]
    elif isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    else:
        return str(obj)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=200,
        content=error_response(
            "Validation failed",
            data={
                "httpStatus": 422,
                "errors": make_json_serializable(exc.errors()),
            },
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception("Unhandled server error")
    return JSONResponse(
        status_code=200,
        content=error_response(
            "Internal server error",
            data={"httpStatus": 500},
        ).model_dump(),
    )

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

app.include_router(build_router())

_original_openapi = app.openapi




def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = _original_openapi()
    
    # 1. Bearer Auth scheme for Firebase ID Tokens
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Use Firebase ID token in the Authorization header (Format: Bearer <token>).",
    }
    
    
    app.openapi_schema = schema
    return schema

app.openapi = custom_openapi



# for route in app.routes:
#     if hasattr(route, "methods"):
#         print(route.path, route.methods, route.name)