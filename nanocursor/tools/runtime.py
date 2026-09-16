"""Per-call execution state; shared tool instances must not store an agent's cwd."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class ToolRuntimeContext:
    cwd: Path
    agent_id: str
    sandbox_root: Path | None = None
    spawn_allowed: bool = True
    file_versions: dict[str, Any] = field(default_factory=dict)
    expected_versions: dict[str, Any] | None = None


_context: ContextVar[ToolRuntimeContext | None] = ContextVar("tool_runtime", default=None)


def current_runtime() -> ToolRuntimeContext | None:
    return _context.get()


@contextmanager
def bind_runtime(context: ToolRuntimeContext) -> Iterator[None]:
    token = _context.set(context)
    try:
        yield
    finally:
        _context.reset(token)


def resolve_workspace_path(raw_path: str | Path) -> Path:
    context = current_runtime()
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (context.cwd if context else Path.cwd()) / path
    path = path.resolve()
    if context and context.sandbox_root is not None:
        if not path.is_relative_to(context.sandbox_root.resolve()):
            raise PermissionError(f"Path {path} is outside isolated workspace {context.sandbox_root}")
    return path


def normalize_local_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    key = {"ReadFile": "file_path", "WriteFile": "file_path", "EditFile": "file_path",
           "Glob": "path", "Grep": "path"}.get(tool_name)
    if key:
        arguments = {**arguments, key: str(resolve_workspace_path(arguments[key]))}
    return arguments
