import pytest
import pytest_asyncio
from core.db.session import engine

@pytest_asyncio.fixture(autouse=True)
async def cleanup_db_connections():
    yield
    await engine.dispose()
