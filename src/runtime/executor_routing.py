"""Command-level routing between Python subprocess and Go executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.runtime.runtime_feature_flags import (
    executor_go_command_patterns,
    executor_go_min_timeout_seconds,
    executor_python_command_patterns,
    executor_routing_mode,
    go_executor_enabled,
)

ExecutorBackend = Literal["python_subprocess", "go_executor"]


@dataclass(frozen=True)
class ExecutorRouteDecision:
    backend: ExecutorBackend
    reason: str
    requires_go: bool = False
    expected_long_running: bool = False
    requires_streaming: bool = False
    risky: bool = False

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "reason": self.reason,
            "requires_go": self.requires_go,
            "expected_long_running": self.expected_long_running,
            "requires_streaming": self.requires_streaming,
            "risky": self.risky,
        }


def choose_executor_backend(
    command: str,
    timeout_seconds: int | float,
    permission_level: str = "shell_safe",
    requires_streaming: bool = False,
    env: dict[str, str] | None = None,
) -> ExecutorRouteDecision:
    """Choose the command execution backend without running the command."""
    normalized = _normalize(command)
    mode = executor_routing_mode()
    risky = str(permission_level or "").lower() in {"shell_risky", "risky", "risky_write"}

    if env is not None:
        return ExecutorRouteDecision(
            backend="python_subprocess",
            reason="custom environment variables require Python subprocess compatibility",
        )

    if mode == "never":
        return ExecutorRouteDecision("python_subprocess", "executor routing mode is never")

    if not go_executor_enabled():
        return ExecutorRouteDecision("python_subprocess", "Go executor feature flag is disabled")

    if mode == "always":
        return ExecutorRouteDecision("go_executor", "executor routing mode is always", requires_go=True)

    if risky:
        return ExecutorRouteDecision(
            "go_executor",
            "risky shell permission benefits from Go executor supervision",
            requires_go=True,
            risky=True,
        )

    if requires_streaming:
        return ExecutorRouteDecision(
            "go_executor",
            "streaming command output requested",
            requires_go=True,
            requires_streaming=True,
        )

    if _matches_any(normalized, executor_python_command_patterns()):
        return ExecutorRouteDecision("python_subprocess", "matched low-latency Python command pattern")

    if _matches_any(normalized, executor_go_command_patterns()):
        return ExecutorRouteDecision(
            "go_executor",
            "matched long-running test/build command pattern",
            expected_long_running=True,
        )

    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        timeout = 120.0
    if timeout <= executor_go_min_timeout_seconds():
        return ExecutorRouteDecision("python_subprocess", "timeout is below Go executor threshold")

    return ExecutorRouteDecision("python_subprocess", "default to low-overhead Python subprocess")


def executor_routing_policy() -> dict:
    return {
        "mode": executor_routing_mode(),
        "go_enabled": go_executor_enabled(),
        "go_min_timeout_seconds": executor_go_min_timeout_seconds(),
        "go_patterns": executor_go_command_patterns(),
        "python_patterns": executor_python_command_patterns(),
    }


def _normalize(command: str) -> str:
    return " ".join(str(command or "").strip().lower().split())


def _matches_any(command: str, patterns: list[str]) -> bool:
    return any(command == pattern or command.startswith(f"{pattern} ") for pattern in patterns)
