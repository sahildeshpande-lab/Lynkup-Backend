from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.lifespan import lifespan
from core.routes import build_router
from core.security.auth import bearer_scheme


AUTH_TAG = "1] User Registration, Authentication & Onboarding"
USER_TAG = "2] User Management"
DISCOVERY_TAG = "3] Search & Discovery"
ADMIN_TAG = "4] Admin Management"

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
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
(BASE_DIR / "static" / "uploads").mkdir(parents=True, exist_ok=True)
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