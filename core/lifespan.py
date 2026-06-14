from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.db.config import settings as db_settings
from core.db.init import init_db, seed_dummy_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    if db_settings.auto_init_db:
        await init_db()
        if db_settings.seed_dummy_data:
            await seed_dummy_data()
            
    # Start the email sender background cron task
    from core.email_service import cron_send_emails
    email_cron_task = asyncio.create_task(cron_send_emails())
    
    yield
    
    # Cancel the task on shutdown
    email_cron_task.cancel()
    try:
        await email_cron_task
    except asyncio.CancelledError:
        pass
