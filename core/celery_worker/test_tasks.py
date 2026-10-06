from sqlalchemy import text

from core.celery_worker.celery_app import celery_app
from core.jobs.runtime import create_worker_runtime


@celery_app.task(name="kampulynk.test_db")
def test_db_task():
    runtime = create_worker_runtime()

    try:

        async def check_database():
            async with runtime.session_factory() as session:
                result = await session.execute(text("SELECT 1"))
                return result.scalar_one()

        result = runtime.runner.run(check_database())

        print(f"Database test result: {result}")

        return result

    finally:
        runtime.close()
