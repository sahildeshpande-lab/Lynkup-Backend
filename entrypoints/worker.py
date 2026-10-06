"""Start a worker: python -m entrypoints.worker [queue ...].

Queue values may be separated by spaces or commas. With no arguments the
worker consumes every queue defined in CeleryTaskQueue.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Support running this file directly as well as with python -m.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.celery_worker.config import CeleryTaskQueue
from core.celery_worker.config import settings as celery_settings


WORKER_LOG_LEVEL = "INFO"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Start the KampuLynk Celery worker.")
    parser.add_argument(
        "queues", nargs="*", help="Queue values separated by spaces or commas."
    )
    parser.add_argument(
        "-Q", "--queues", dest="queue_options", nargs="+",
        help="Queue values separated by spaces or commas (alternative to positional queues).",
    )
    args = parser.parse_args(argv)
    if args.queues and args.queue_options:
        parser.error("Use positional queues or --queues, not both.")
    queue_args = args.queue_options or args.queues

    queues = CeleryTaskQueue.queue_names_str()
    if queue_args:
        requested = [queue.strip() for arg in queue_args for queue in arg.split(",")]
        valid = {queue.value for queue in CeleryTaskQueue}
        invalid = [queue for queue in requested if queue not in valid]
        if invalid:
            raise RuntimeError(
                f"Unknown queue(s): {', '.join(repr(queue) for queue in invalid)}. "
                f"Valid queues: {CeleryTaskQueue.queue_names_str()}"
            )
        queues = ",".join(dict.fromkeys(requested))

    # Validate before importing the app or starting any worker services.
    from core.logging_config import configure_logging

    configure_logging()

    from core.celery_worker.celery_app import celery_app

    worker_args = [
        "worker",
        f"--loglevel={WORKER_LOG_LEVEL}",
        f"--pool={celery_settings.worker_pool}",
        f"--concurrency={celery_settings.worker_concurrency}",
        f"--queues={queues}",
    ]
    if celery_settings.beat_enabled:
        worker_args.append("--beat")
        worker_args.extend([
            "--scheduler",
            "core.celery_worker.scheduler.RecoveringRedBeatScheduler",
        ])

    celery_app.worker_main(worker_args)


if __name__ == "__main__":
    main()
