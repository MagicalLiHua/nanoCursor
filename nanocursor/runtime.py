"""User-owned application paths and side-effect-free version discovery."""
from __future__ import annotations

import logging
import os
import re
from importlib.metadata import PackageNotFoundError, version
from logging.handlers import RotatingFileHandler
from pathlib import Path


def app_home() -> Path:
    override = os.environ.get("NANOCURSOR_HOME")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            from nanocursor.validator import ConfigError
            raise ConfigError("NANOCURSOR_HOME must be an absolute directory path")
        return path.resolve()
    return Path.home() / ".nanocursor"


def get_version() -> str:
    try:
        return version("nanocursor")
    except PackageNotFoundError:
        # Source archives still have project metadata; never require Git.
        import tomllib
        try:
            path = Path(__file__).resolve().parent.parent / "pyproject.toml"
            return tomllib.loads(path.read_text())["project"]["version"] + "+source"
        except (OSError, KeyError, ValueError):
            return "unknown+source"


def redact(text: str) -> str:
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s,]+", r"\1[redacted]", text)
    return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", text)


def configure_logging() -> None:
    from nanocursor.storage import private_directory
    directory = app_home() / "logs"
    private_directory(directory)
    path = directory / "debug.log"
    if path.is_symlink():
        raise OSError("Log file must not be a symbolic link")
    class SafeFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            return redact(super().format(record))

    class PrivateLogHandler(RotatingFileHandler):
        def _open(self):
            fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, "a", encoding="utf-8")

    handler = PrivateLogHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(SafeFormatter("%(asctime)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    for name in ("httpx", "httpcore", "openai", "anthropic"):
        logging.getLogger(name).setLevel(logging.ERROR)
