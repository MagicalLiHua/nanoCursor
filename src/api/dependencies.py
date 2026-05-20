"""Shared dependencies for API route modules."""

from __future__ import annotations

from fastapi import HTTPException


def get_workspace() -> str:
    import src.infra.config as config_module
    return config_module.WORKSPACE_DIR


def get_event_store():
    from src.api.services.event_store import get_event_store as _get
    return _get()


def get_run_manager():
    import api_server
    return api_server.run_manager


def raise_404(message: str):
    raise HTTPException(status_code=404, detail=message)


def raise_400(message: str):
    raise HTTPException(status_code=400, detail=message)
