from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from apps.accounts.services import AccountExistsException
from common.exceptions import ApiError
from common.responses import error_response, serialize_response
from core.lifespan import lifespan
from core.logging_config import configure_logging
from core.routes import build_router

try:
    from ddtrace import patch_all
    patch_all()
except Exception:
    # do not raise error if you can not patch
    pass

configure_logging()

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
    # Public signup / social-auth only allow role "user".
    if field in ("role", "user") and "Input should be 'user'" in str(msg):
        return "Invalid role"
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
    return serialize_response(error_response(message))


def _api_error_status_code(message: str) -> int:
    lowered = message.lower()
    if "account doesn't exist" in lowered:
        return 401
    if "account already exists" in lowered:
        return 401
    if "this user is not allowed" in lowered:
        return 401
    if any(
        phrase in lowered
        for phrase in (
            "missing access token",
            "invalid access token",
            "invalid firebase",
            "maximum number of accounts",
            "device has been reached",
            "account limit",
            "request authentication failed",
            "session expired",
            "signing key registration",
        )
    ) or _is_account_status_message(message):
        return 401
    if any(
        phrase in lowered
        for phrase in (
            "insufficient permissions",
        )
    ):
        return 403
    if "rate limit exceeded" in lowered:
        return 429
    return 200


def _is_account_status_message(message: str) -> bool:
    lowered = message.lower()
    if "account is currently" in lowered:
        return False
    return any(
        phrase in lowered
        for phrase in (
            "your account is",
            "account deleted",
            "account is pending",
            "account is suspended",
            "account is banned",
            "account is deleting",
            "account is not active",
            "account is blocked",
        )
    )


@app.exception_handler(HTTPException)
async def legacy_http_exception_handler(_request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    if _is_account_status_message(detail):
        return JSONResponse(status_code=401, content=_error_json(detail))
    status_code = exc.status_code if exc.status_code >= 400 else 200
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
        )
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request, exc: Exception):
    logger.exception("Unhandled server error: %s", exc)
    return JSONResponse(status_code=500, content=_error_json("Internal server error"))


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
        for prop_name, prop in body_schema.get("properties", {}).items():
            if not isinstance(prop, dict):
                continue
            if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
                items = prop["items"]
                if prop_name in {"file", "files"} and items.get("type") == "string":
                    prop["items"] = {"type": "string", "format": "binary"}
            elif prop_name in {"file", "files"} and prop.get("type") == "string" and "contentMediaType" in prop:
                prop["format"] = "binary"
                prop.pop("contentMediaType", None)

def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = _original_openapi()

    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "Bearer token: Firebase ID token (mobile) or Web Admin access JWT. "
            "Signed admin routes also need the Admin* header schemes below."
        ),
    }
    # Ensure Swagger Authorize shows Web Admin RSA signing headers
    # (also registered via APIKeyHeader on require_admin_signed_request).
    admin_signing_schemes = {
        "AdminKeyId": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Key-ID",
            "description": "Signing key UUID from key-register / login.",
        },
        "AdminSessionId": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Session-Id",
            "description": "Admin session UUID from login (must match JWT session).",
        },
        "AdminTimestamp": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Timestamp",
            "description": "Unix epoch seconds (within signing timestamp tolerance).",
        },
        "AdminNonce": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Nonce",
            "description": "Unique base64url nonce per request.",
        },
        "AdminSignature": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Signature",
            "description": "Base64 RSA-PSS/SHA-256 signature of the canonical request.",
        },
    }
    for name, definition in admin_signing_schemes.items():
        schemes[name] = {**schemes.get(name, {}), **definition}

    _patch_multipart_file_schemas(schema)

    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "entrypoints.api:app",
        host=os.getenv("HOST", "0.0.0.0"),  # nosec B104 -- container deployment, host from env
        port=int(os.getenv("PORT", "8000")),
        reload=_env_bool("RELOAD"),
    )


if __name__ == "__main__":
    main()
