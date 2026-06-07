"""Feature flags for optional runtime integrations."""

from __future__ import annotations

import os


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def go_runtime_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_RUNTIME_ENABLED", False)


def go_runtime_fallback_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_RUNTIME_FALLBACK", True)


def go_runtime_url() -> str:
    return os.getenv("NANOCURSOR_GO_RUNTIME_URL", "http://127.0.0.1:8120").rstrip("/")


def go_runtime_timeout_ms() -> int:
    raw = os.getenv("NANOCURSOR_GO_RUNTIME_TIMEOUT_MS", "30000")
    try:
        return max(1000, min(int(raw), 600_000))
    except ValueError:
        return 30_000


def go_indexer_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_INDEXER_ENABLED", True)


def go_indexer_fallback_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_INDEXER_FALLBACK", True)


def go_indexer_addr() -> str:
    return os.getenv("NANOCURSOR_GO_INDEXER_ADDR", os.getenv("INDEXER_GRPC_ADDR", "localhost:50051"))


def go_indexer_failure_cooldown_seconds() -> float:
    raw = os.getenv("NANOCURSOR_GO_INDEXER_FAILURE_COOLDOWN_SECONDS", "10")
    try:
        return max(0.0, min(float(raw), 300.0))
    except ValueError:
        return 10.0


def go_filetools_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_FILETOOLS_ENABLED", True)


def go_filetools_fallback_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_FILETOOLS_FALLBACK", True)


def go_filetools_addr() -> str:
    return os.getenv("NANOCURSOR_GO_FILETOOLS_ADDR", os.getenv("FILETOOLS_GRPC_ADDR", "localhost:50054"))


def go_executor_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_EXECUTOR_ENABLED", False)


def go_executor_fallback_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_EXECUTOR_FALLBACK", True)


def go_executor_addr() -> str:
    return os.getenv("NANOCURSOR_GO_EXECUTOR_ADDR", "localhost:50055")
