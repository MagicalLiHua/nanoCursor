"""Official ASGI entrypoint for nanoCursor."""

from __future__ import annotations

from src.api.app import create_app


app = create_app()

__all__ = ["app"]
