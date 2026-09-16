from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from nanocursor.tools.file_io import path_lock, read_snapshot
from nanocursor.tools.runtime import resolve_workspace_path
from nanocursor.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from nanocursor.cache import FileCache
    from nanocursor.tools.file_state_cache import FileStateCache


class Params(BaseModel):
    file_path: str = Field(description="Absolute or relative path to the file to read")
    offset: int = Field(default=0, description="Line offset to start reading from (0-based)")
    limit: int = Field(default=2000, description="Maximum number of lines to read")


class ReadFile(Tool):
    name = "ReadFile"
    description = "Read a file and return its contents with line numbers."
    params_model = Params
    category = "read"
    is_concurrency_safe = True


    def __init__(self, file_cache: FileCache | None = None, file_state_cache: FileStateCache | None = None) -> None:
        self._cache = file_cache
        self._state_cache = file_state_cache


    async def execute(self, params: Params) -> ToolResult:
        path = resolve_workspace_path(params.file_path)
        if not path.exists():
            return ToolResult(output=f"Error: file not found: {params.file_path}", is_error=True)
        if not path.is_file():
            return ToolResult(output=f"Error: not a file: {params.file_path}", is_error=True)

        resolved = str(path.resolve())

        try:
            with path_lock(path):
                text, version = read_snapshot(path, self._cache)
                if self._state_cache is not None:
                    self._state_cache.record_version(resolved, version)
        except Exception as e:
            return ToolResult(output=f"Error reading file: {e}", is_error=True)

        lines = text.splitlines()
        selected = lines[params.offset : params.offset + params.limit]
        numbered = [f"{i + params.offset + 1}\t{line}" for i, line in enumerate(selected)]
        return ToolResult(output="\n".join(numbered))
