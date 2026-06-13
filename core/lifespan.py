from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.db.config import settings as db_settings
from core.db.init import init_db, seed_dummy_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    if db_settings.auto_init_db:
        await init_db()
        if db_settings.seed_dummy_data:
            await seed_dummy_data()
    yield
