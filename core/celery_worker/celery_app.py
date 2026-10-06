import ssl

import certifi
from celery import Celery
from celery.schedules import crontab

from apps.moderation.config import settings as auto_moderation_settings
from apps.user_deletion.config import settings as deletion_settings
from core.celery_worker.config import CeleryTaskQueue
from core.celery_worker.config import settings as celery_settings
from core.database import models as _models  # noqa: F401
from core.jobs.config import settings as reconciliation_settings

celery_app = Celery(
    "kampulynk",
    broker=celery_settings.broker_url,
    backend=celery_settings.broker_url,
)

celery_app.conf.update(
    task_default_queue=CeleryTaskQueue.BACKGROUND_QUEUE.value,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.imports = (
    "core.celery_worker.test_tasks",
    "apps.export.tasks",
    "apps.export.cleanup_tasks",
    "apps.recommendations.tasks",
    "core.email_tasks",
    "apps.moderation.tasks",
    "apps.user_deletion.tasks",
    "apps.learningspotlight.tasks",
    "apps.profiles.tasks",
    "apps.notifications.tasks",
    "core.jobs.reconcile_tasks",
)

celery_app.conf.redbeat_redis_url=celery_settings.broker_url
celery_app.conf.redbeat_key_prefix = 'redbeat:'

if celery_settings.redis_ssl_enabled:
    # Use a CA bundle even when the Python installation has no system trust store.
    redis_ssl_options = {
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
        "ssl_ca_certs": certifi.where(),
    }
    celery_app.conf.broker_use_ssl = redis_ssl_options.copy()
    celery_app.conf.redis_backend_use_ssl = redis_ssl_options.copy()
    celery_app.conf.redbeat_connect_options = redis_ssl_options.copy()

celery_app.conf.beat_schedule = {
    "kampulynk.recommendations.tick": {
        "task": "kampulynk.recommendations.tick",
        "schedule": 3600.0,
        "options": {"queue": CeleryTaskQueue.BACKGROUND_QUEUE.value},
    },
    "kampulynk.export.cleanup": {
        "task": "kampulynk.export.cleanup",
        "schedule": 3600.0,
        "options": {"queue": CeleryTaskQueue.EXPORTS_QUEUE.value},
    },
    "kampulynk.email.transactional.tick": {
        "task": "kampulynk.email.transactional.tick",
        "schedule": 60.0,
        "options": {"queue": CeleryTaskQueue.TRANSACTIONAL_QUEUE.value},
    },

    "kampulynk.email.bulk.tick": {
        "task": "kampulynk.email.bulk.tick",
        "schedule": 60.0,
        "options": {"queue": CeleryTaskQueue.BULK_EMAIL_QUEUE.value},
    },

    "kampulynk.moderation.tick": {
        "task": "kampulynk.moderation.tick",
        "schedule": float(auto_moderation_settings.cron_interval_seconds),
        "options": {"queue": CeleryTaskQueue.BACKGROUND_QUEUE.value},
    },

    "kampulynk.deletion.tick": {
        "task": "kampulynk.deletion.tick",
        "schedule": float(
            max(1, deletion_settings.account_deletion_cron_interval_hours) * 3600
        ),
        "options": {"queue": CeleryTaskQueue.BACKGROUND_QUEUE.value},
    },

    "kampulynk.spotlight.tick": {
        "task": "kampulynk.spotlight.tick",
        "schedule": crontab(hour=0, minute=0),
        "options": {"queue": CeleryTaskQueue.SPOTLIGHTS_QUEUE.value},
    },

    "kampulynk.reconcile.tick": {
        "task": "kampulynk.reconcile.tick",
        "schedule": float(reconciliation_settings.interval_seconds),
        "options": {"queue": CeleryTaskQueue.BACKGROUND_QUEUE.value},
    },
}
