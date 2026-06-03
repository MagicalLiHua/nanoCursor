"""Official ASGI entrypoint for nanoCursor.

The legacy root-level ``api_server.py`` still owns a few compatibility
exports and inline routes while the backend is being modularized. New runtime
commands should import the ASGI app from here so the public entrypoint lives
inside the backend package.
"""

from __future__ import annotations

from api_server import app

__all__ = ["app"]
