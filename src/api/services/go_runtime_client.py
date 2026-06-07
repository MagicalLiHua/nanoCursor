"""Compatibility re-export for the Go Runtime client."""

from src.runtime.go_runtime_client import (  # noqa: F401
    GoRuntimeError,
    GoRuntimeUnavailable,
    health,
    normalize_command_result,
    run_command_via_go_runtime,
)
