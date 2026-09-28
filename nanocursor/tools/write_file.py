from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from nanocursor.tools.file_io import atomic_write, file_history_context, path_lock, validate_regular_path
from nanocursor.tools.runtime import current_runtime, resolve_workspace_path
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
        try:
            context = current_runtime()
            validate_regular_path(params.file_path, cwd=context.cwd if context else None)
            path = resolve_workspace_path(params.file_path)
            history, operation_id = file_history_context(self.file_history)
        except Exception as exc:
            return ToolResult(output=f"Error writing file: {exc}", is_error=True)

        with path_lock(path):
            if self._state_cache is not None and (path.exists() or self._state_cache.has_read(str(path))):
                resolved = str(path.resolve())
                ok, err_msg = self._state_cache.check(resolved)
                if not ok:
                    return ToolResult(output=err_msg, is_error=True)

            try:
                edit_id = history.prepare_edit(str(path), params.content, operation_id=operation_id) if history else None
                if self._state_cache is not None and (path.exists() or self._state_cache.has_read(str(path))):
                    ok, err_msg = self._state_cache.check(str(path.resolve()))
                    if not ok:
                        return ToolResult(output=err_msg, is_error=True)
                if history:
                    history.verify_prepared(edit_id)
                atomic_write(path, params.content)
                if history:
                    history.applied_edit(edit_id)
                if self._cache is not None:
                    self._cache.invalidate(str(path.resolve()))
                if self._state_cache:
                    self._state_cache.update(str(path.resolve()))
            except Exception as e:
                return ToolResult(output=f"Error writing file: {e}", is_error=True)
        coverage = "\nThis project-external edit is not covered by workspace checkpoints." if history and edit_id is None else ""
        return ToolResult(output=f"Successfully wrote to {params.file_path}{coverage}")
