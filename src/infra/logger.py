"""Compatibility facade for nanoCursor structured logging.

Older modules import ``logger`` from this module.  Keep that stable API while
delegating all configuration to :mod:`src.infra.logging`.
"""

from __future__ import annotations

import logging
import os

from src.infra.logging import StructuredFormatter, setup_structured_logging


def setup_logger(
    name: str = "nanoCursor",
    level: str = "INFO",
    log_file: str | None = None,
) -> logging.Logger:
    """Return a structured logger while preserving the legacy helper API."""
    if name == "nanoCursor":
        configured = setup_structured_logging(level)
    else:
        setup_structured_logging(level)
        configured = logging.getLogger(name)
        configured.setLevel(getattr(logging, level.upper(), logging.INFO))

    if log_file:
        absolute_path = os.path.abspath(log_file)
        already_configured = any(
            isinstance(handler, logging.FileHandler)
            and os.path.abspath(handler.baseFilename) == absolute_path
            for handler in configured.handlers
        )
        if not already_configured:
            directory = os.path.dirname(absolute_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            file_handler = logging.FileHandler(absolute_path, encoding="utf-8")
            file_handler.setFormatter(StructuredFormatter())
            configured.addHandler(file_handler)
    return configured


logger = setup_logger()


__all__ = ["logger", "setup_logger"]
