from __future__ import annotations

import json
import logging
import sys

from core.logging_config import JsonLogFormatter, configure_logging


def test_configure_logging_adds_console_handler_and_info_level(monkeypatch):
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level

    try:
        root_logger.handlers.clear()
        monkeypatch.delenv("LOG_LEVEL", raising=False)

        configure_logging()

        assert root_logger.getEffectiveLevel() == logging.INFO
        assert len(root_logger.handlers) == 1
        assert root_logger.handlers[0].level == logging.NOTSET
        assert isinstance(root_logger.handlers[0].formatter, JsonLogFormatter)
    finally:
        root_logger.handlers[:] = original_handlers
        root_logger.setLevel(original_level)


def test_json_log_formatter_includes_standard_fields_extra_and_exception():
    formatter = JsonLogFormatter()
    logger = logging.getLogger("tests.logging")

    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()

    record = logger.makeRecord(
        logger.name,
        logging.ERROR,
        "/tmp/example.py",
        42,
        "Request failed for %s",
        ("user-1",),
        exc_info,
        "handle_request",
        extra={"request_id": "req-123", "metadata": {"user_id": 7}},
    )

    payload = json.loads(formatter.format(record))

    assert payload["logger"] == "tests.logging"
    assert payload["message"] == "Request failed for user-1"
    assert payload["level"] == "ERROR"
    assert payload["levelno"] == logging.ERROR
    assert payload["pathname"] == "/tmp/example.py"
    assert payload["lineno"] == 42
    assert payload["funcName"] == "handle_request"
    assert payload["request_id"] == "req-123"
    assert payload["metadata"] == {"user_id": 7}
    assert "ValueError: boom" in payload["exception"]
