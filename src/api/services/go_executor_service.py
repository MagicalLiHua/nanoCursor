"""Status helpers for the optional Go executor sidecar and routing policy."""

from __future__ import annotations

from typing import Literal, TypedDict

from src.runtime.executor_routing import executor_routing_policy
from src.runtime.runtime_feature_flags import (
    go_executor_addr,
    go_executor_enabled,
    go_executor_fallback_enabled,
)


class GoExecutorStatus(TypedDict):
    enabled: bool
    fallback_enabled: bool
    address: str
    healthy: bool
    service: str | None
    version: str | None
    backend: Literal["go", "python"]
    routing_policy: dict
    error: str | None


def get_go_executor_status() -> GoExecutorStatus:
    """Return Go executor availability and command routing policy."""
    enabled = go_executor_enabled()
    fallback_enabled = go_executor_fallback_enabled()
    address = go_executor_addr()
    base: GoExecutorStatus = {
        "enabled": enabled,
        "fallback_enabled": fallback_enabled,
        "address": address,
        "healthy": False,
        "service": None,
        "version": None,
        "backend": "python",
        "routing_policy": executor_routing_policy(),
        "error": None,
    }
    if not enabled:
        return base

    try:
        from src.runtime import executor_client

        original_addr = executor_client.EXECUTOR_ADDR
        try:
            if original_addr != address:
                executor_client.close()
                executor_client.EXECUTOR_ADDR = address
            health = executor_client.health()
        finally:
            if executor_client.EXECUTOR_ADDR != original_addr:
                executor_client.close()
                executor_client.EXECUTOR_ADDR = original_addr
        ok = bool(health.get("ok"))
        return {
            **base,
            "healthy": ok,
            "service": str(health.get("service") or "") or None,
            "version": str(health.get("version") or "") or None,
            "backend": "go" if ok else "python",
            "error": None if ok else "health check returned not ok",
        }
    except Exception as exc:
        return {
            **base,
            "error": str(exc),
        }
