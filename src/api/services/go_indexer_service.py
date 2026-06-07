"""Status helpers for the optional Go indexer sidecar."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

from src.runtime.runtime_feature_flags import (
    go_indexer_addr,
    go_indexer_enabled,
    go_indexer_fallback_enabled,
)


class GoIndexerStatus(TypedDict):
    enabled: bool
    fallback_enabled: bool
    address: str
    healthy: bool
    service: str | None
    version: str | None
    indexed_files: int
    backend: Literal["go", "python"]
    error: str | None


def get_go_indexer_status(timeout_seconds: float = 1.0) -> GoIndexerStatus:
    """Return current Go indexer availability without raising on connection errors."""
    enabled = go_indexer_enabled()
    fallback_enabled = go_indexer_fallback_enabled()
    address = go_indexer_addr()
    base: GoIndexerStatus = {
        "enabled": enabled,
        "fallback_enabled": fallback_enabled,
        "address": address,
        "healthy": False,
        "service": None,
        "version": None,
        "indexed_files": 0,
        "backend": "python",
        "error": None,
    }
    if not enabled:
        return base

    try:
        from src.indexer.indexer_grpc import ProjectIndexClient

        client = ProjectIndexClient(Path.cwd(), server_addr=address)
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
            "indexed_files": int(health.get("indexed_files") or 0),
            "backend": "go" if ok else "python",
            "error": None if ok else "health check returned not ok",
        }
    except Exception as exc:
        return {
            **base,
            "error": str(exc),
        }
