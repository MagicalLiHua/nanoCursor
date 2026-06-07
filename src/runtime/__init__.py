"""nanoCursor runtime: state machines, event schemas, run management."""

from __future__ import annotations

import importlib


def __getattr__(name: str):
    """Lazy-load runtime submodules accessed via package attributes."""
    if name == "taskboard_client":
        module = importlib.import_module(f"{__name__}.taskboard_client")
        globals()[name] = module
        return module
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
