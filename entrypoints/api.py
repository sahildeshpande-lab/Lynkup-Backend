from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from core.lifespan import lifespan
from core.routes import build_router
from core.security.auth import bearer_scheme
from common.responses import error_response
from common.exceptions import ApiError
from apps.accounts.services import AccountExistsException


AUTH_TAG = "1] User Registration, Authentication & Onboarding"
USER_TAG = "2] User Management"
DISCOVERY_TAG = "3] Search & Discovery"
ADMIN_TAG = "4] Admin Management"
CONNECTION_TAG = "5] Connection Managements"


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Validation failed"
    first = errors[0]
    loc = first.get("loc", ())
    field = loc[-1] if loc else "request"
    msg = first.get("msg", "Validation failed")
    if isinstance(field, str) and field not in ("body", "query", "path"):
        return f"Invalid {field}: {msg}"
    return str(msg)


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

import logging

logger = logging.getLogger(__name__)


def _error_json(message: str) -> dict:
    return error_response(message).model_dump()


def _api_error_status_code(message: str) -> int:
    lowered = message.lower()
    if any(
        phrase in lowered
        for phrase in (
            "missing access token",
            "invalid access token",
            "invalid firebase",
        )
    ):
        return 401
    if any(
        phrase in lowered
        for phrase in (
            "account deleted",
            "account is pending",
            "account is suspended",
            "account is banned",
            "account is not active",
            "insufficient permissions",
        )
    ):
        return 403
    return 200


@app.exception_handler(HTTPException)
async def legacy_http_exception_handler(_request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    status_code = exc.status_code if exc.status_code in (401, 403) else 200
    return JSONResponse(status_code=status_code, content=_error_json(detail))


@app.exception_handler(ApiError)
async def api_error_handler(_request, exc: ApiError):
    return JSONResponse(
        status_code=_api_error_status_code(exc.message),
        content=_error_json(exc.message),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request, exc: RequestValidationError):
    return JSONResponse(status_code=200, content=_error_json(_validation_message(exc)))


@app.exception_handler(AccountExistsException)
async def account_exists_exception_handler(_request, exc: AccountExistsException):
    return JSONResponse(
        status_code=200,
        content=_error_json(
            "Account already exists. Please login using your registered method."
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request, exc: Exception):
    logger.exception("Unhandled server error: %s", exc)
    return JSONResponse(status_code=200, content=_error_json("Internal server error"))


BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

app.include_router(build_router())

_original_openapi = app.openapi


def _patch_multipart_file_schemas(schema: dict) -> None:
    """Ensure Swagger UI renders multipart file fields as file pickers, not string arrays."""
    components = schema.get("components", {}).get("schemas", {})
    for body_schema in components.values():
        if not isinstance(body_schema, dict):
            continue
        for prop in body_schema.get("properties", {}).values():
            if not isinstance(prop, dict):
                continue
            if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
                items = prop["items"]
                if items.get("type") == "string":
                    prop["items"] = {"type": "string", "format": "binary"}
            elif prop.get("type") == "string" and "contentMediaType" in prop:
                prop["format"] = "binary"
                prop.pop("contentMediaType", None)


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = _original_openapi()

    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Use Firebase ID token in the Authorization header (Format: Bearer <token>).",
    }

    _patch_multipart_file_schemas(schema)

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
