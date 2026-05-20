"""Structured JSON logging for nanoCursor.

Every log line is a JSON object with standard fields so logs can be
ingested by log aggregators or searched with `jq`.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": time.time(),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
        }

        # Attach extra fields passed via `extra=` dict
        for key in (
            "request_id", "thread_id", "workspace_id",
            "duration_ms", "status_code", "path", "method",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        if record.exc_info and record.exc_info[1]:
            payload["exception"] = str(record.exc_info[1])

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_structured_logging(level: str = "INFO") -> logging.Logger:
    """Configure the root nanoCursor logger for structured JSON output."""
    logger = logging.getLogger("nanoCursor")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)

    return logger


def get_logger() -> logging.Logger:
    """Return the nanoCursor structured logger (lazy init)."""
    logger = logging.getLogger("nanoCursor")
    if not logger.handlers:
        return setup_structured_logging()
    return logger


def log_event(
    event: str,
    level: str = "INFO",
    **extra: Any,
) -> None:
    """Log a single structured event with optional extra fields."""
    logger = get_logger()
    log_method = getattr(logger, level.lower(), logger.info)
    log_method(event, extra=extra)
