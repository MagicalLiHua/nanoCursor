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


def go_mcp_gateway_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_MCP_GATEWAY_ENABLED", False)


def go_mcp_gateway_fallback_enabled() -> bool:
    return env_flag("NANOCURSOR_GO_MCP_GATEWAY_FALLBACK", True)


def go_mcp_gateway_addr() -> str:
    return os.getenv("NANOCURSOR_GO_MCP_GATEWAY_ADDR", os.getenv("NANOCURSOR_MCP_ADDR", "localhost:50056"))


def executor_routing_mode() -> str:
    raw = os.getenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "auto").strip().lower()
    return raw if raw in {"auto", "always", "never"} else "auto"


def executor_go_min_timeout_seconds() -> int:
    raw = os.getenv("NANOCURSOR_EXECUTOR_GO_MIN_TIMEOUT_SECONDS", "2")
    try:
        return max(1, min(int(raw), 600))
    except ValueError:
        return 2


def executor_go_command_patterns() -> list[str]:
    return _csv_env(
        "NANOCURSOR_EXECUTOR_GO_COMMAND_PATTERNS",
        "pytest,npm test,npm run build,npm run lint,go test,pnpm test,yarn test,ruff,mypy",
    )


def executor_python_command_patterns() -> list[str]:
    return _csv_env(
        "NANOCURSOR_EXECUTOR_PYTHON_COMMAND_PATTERNS",
        "pwd,ls,cat,echo,python -c,node -e,git status",
    )


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]
