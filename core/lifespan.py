from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
import time
from datetime import datetime, timezone
from fastapi import FastAPI

from core.database.config import settings as db_settings
from core.database.init import init_db
from core.database.migrations import run_db_migrations_programmatically


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = logging.getLogger(__name__)
    startup_started = time.perf_counter()

    if db_settings.auto_init_db:
        await init_db()
        # try:
        #     logger.info("Start DB Migrations")
        #     await run_db_migrations_programmatically()
        #     logger.info("Migrations completed successfully")
        # except Exception as e:
        #     logger.exception(f"Migration startup failed: {e}")
        #     raise
        # logger.info(f"Initialized database at {db_settings.db_host}")

    # Load email settings from .env early so SendGrid config is available.
    from core.email.config import settings as email_settings

    logger.info("[%s] Importing recommendation algorithm module...", _timestamp())
    from apps.recommendation.services import algorithm as recommendation_algorithm

    logger.info("[%s] Recommendation algorithm module imported successfully.", _timestamp())

    def _initialize_recommendation_models_in_thread() -> None:
        thread_logger = logging.getLogger(__name__)
        thread_logger.info(
            "[%s] Recommendation model initialization worker thread started.",
            _timestamp(),
        )
        recommendation_algorithm.initialize_models()
        thread_logger.info(
            "[%s] Recommendation model initialization worker thread finished.",
            _timestamp(),
        )

    model_init_started = time.perf_counter()
    try:
        logger.info("[%s] Submitting recommendation model initialization to worker thread...", _timestamp())
        await asyncio.to_thread(_initialize_recommendation_models_in_thread)
        logger.info(
            "[%s] Recommendation model worker thread completed (%.2f sec).",
            _timestamp(),
            time.perf_counter() - model_init_started,
        )
        logger.info("[%s] Recommendation models loaded successfully.", _timestamp())
    except Exception:
        logger.exception(
            "[%s] Recommendation model initialization failed during application startup "
            "after %.2f sec.",
            _timestamp(),
            time.perf_counter() - model_init_started,
        )
        raise

    logger.info(
        "[%s] Recommendation startup phase completed (Total: %.2f sec).",
        _timestamp(),
        time.perf_counter() - startup_started,
    )

    if email_settings.is_sendgrid_configured:
        logger.info("SendGrid email delivery is configured.")
    else:
        logger.warning(
            "SendGrid is not fully configured; transactional emails will be simulated."
        )

    # Start the email sender background cron task
    from core.email_service import cron_send_emails
    email_cron_task = asyncio.create_task(cron_send_emails())

    logger.info(
        "[%s] Application Started Successfully (Total startup: %.2f sec).",
        _timestamp(),
        time.perf_counter() - startup_started,
    )
    yield

    # Cancel the task on shutdown
    email_cron_task.cancel()
    try:
        await email_cron_task
    except asyncio.CancelledError:
        pass

    logger.info("Application Shutdown Completed")
