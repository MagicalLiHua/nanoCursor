from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from nanocursor.tools.file_io import atomic_write, path_lock
from nanocursor.tools.runtime import resolve_workspace_path
from nanocursor.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from nanocursor.cache import FileCache
    from nanocursor.tools.file_state_cache import FileStateCache


class Params(BaseModel):
    file_path: str = Field(description="Path to the file to write")
    content: str = Field(description="Content to write to the file")


class WriteFile(Tool):
    name = "WriteFile"
    description = (
        "Write content to a file, creating parent directories if needed. Overwrites existing files.\n"
        "You MUST read existing files with ReadFile before overwriting them. This tool will fail otherwise."
    )
    params_model = Params
    category = "write"


    def __init__(self, file_cache: FileCache | None = None, file_history: Any = None, file_state_cache: FileStateCache | None = None) -> None:
        self._cache = file_cache
        self.file_history = file_history
        self._state_cache = file_state_cache


    async def execute(self, params: Params) -> ToolResult:
        path = resolve_workspace_path(params.file_path)

        with path_lock(path):
            if self._state_cache is not None and (path.exists() or self._state_cache.has_read(str(path))):
                resolved = str(path.resolve())
                ok, err_msg = self._state_cache.check(resolved)
                if not ok:
                    return ToolResult(output=err_msg, is_error=True)

            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if self.file_history is not None:
                    self.file_history.track_edit(str(path))
                atomic_write(path, params.content)
                if self._cache is not None:
                    self._cache.invalidate(str(path.resolve()))
                if self._state_cache:
                    self._state_cache.update(str(path.resolve()))
            except Exception as e:
                return ToolResult(output=f"Error writing file: {e}", is_error=True)
        return ToolResult(output=f"Successfully wrote to {params.file_path}")
