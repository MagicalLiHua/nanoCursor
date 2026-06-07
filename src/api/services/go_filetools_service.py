"""Status helpers for the optional Go filetools sidecar."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

from src.runtime.runtime_feature_flags import (
    go_filetools_addr,
    go_filetools_enabled,
    go_filetools_fallback_enabled,
)


class GoFiletoolsStatus(TypedDict):
    enabled: bool
    fallback_enabled: bool
    address: str
    healthy: bool
    service: str | None
    version: str | None
    backend: Literal["go", "python"]
    error: str | None


def get_go_filetools_status(timeout_seconds: float = 1.0) -> GoFiletoolsStatus:
    """Return current Go filetools availability without raising on connection errors."""
    enabled = go_filetools_enabled()
    fallback_enabled = go_filetools_fallback_enabled()
    address = go_filetools_addr()
    base: GoFiletoolsStatus = {
        "enabled": enabled,
        "fallback_enabled": fallback_enabled,
        "address": address,
        "healthy": False,
        "service": None,
        "version": None,
        "backend": "python",
        "error": None,
    }
    if not enabled:
        return base

    try:
        from src.tools.filetools_client import FileToolsClient

        client = FileToolsClient(str(Path.cwd()), server_addr=address)
        try:
            health = client.health_sync(timeout_seconds=timeout_seconds)
        finally:
            client.close()
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

