"""One-time backfill of profiles.extracted_keywords for legacy users.

Run manually after deploying the recommendation keyword feature:

    python scripts/backfill_profiles.py

This is not an API endpoint and is not scheduled on startup.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.recommendation.services.profile_backfill_service import ProfileBackfillService
from core.database.init import init_db
from core.logging_config import configure_logging


async def main() -> None:
    configure_logging()
    logging.getLogger(__name__).info("Starting profile keyword backfill...")
    await init_db()
    await ProfileBackfillService().backfill_existing_profiles()
    logging.getLogger(__name__).info("Profile keyword backfill finished.")


if __name__ == "__main__":
    asyncio.run(main())
