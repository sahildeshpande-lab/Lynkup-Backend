from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from typing import Any


STANDARD_LOG_RECORD_ATTRIBUTES = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
)


def _json_default(value: Any) -> str:
    return repr(value)


class JsonLogFormatter(logging.Formatter):
    """Format log records as structured JSON for console logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "logger": record.name,
            "message": record.getMessage(),
            "level": record.levelname,
            "levelno": record.levelno,
            "pathname": record.pathname,
            "filename": record.filename,
            "module": record.module,
            "lineno": record.lineno,
            "funcName": record.funcName,
            "process": record.process,
            "processName": record.processName,
            "thread": record.thread,
            "threadName": record.threadName,
            "created": record.created,
            "msecs": record.msecs,
            "relativeCreated": record.relativeCreated,
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        for key, value in record.__dict__.items():
            if key not in STANDARD_LOG_RECORD_ATTRIBUTES and key not in payload:
                payload[key] = value

        return json.dumps(payload, default=_json_default, separators=(",", ":"))


def configure_logging(default_level: str = "INFO") -> None:
    """Configure application loggers to emit to the console."""
    level_name = os.getenv("LOG_LEVEL", default_level).strip().upper()
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        level = logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.NOTSET)
        handler.setFormatter(JsonLogFormatter())
        root_logger.addHandler(handler)

    logging.captureWarnings(True)
