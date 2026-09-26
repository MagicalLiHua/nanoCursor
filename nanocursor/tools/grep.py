from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from nanocursor.tools.runtime import resolve_workspace_path
from nanocursor.tools.base import MAX_OUTPUT_CHARS, SKIP_DIRS, Tool, ToolResult
from nanocursor.tools.file_io import read_bounded_text


class Params(BaseModel):
    pattern: str = Field(description="Regex pattern to search for")
    path: str = Field(default=".", description="Base directory to search from")
    include: str = Field(default="", description="Glob filter for filenames (e.g. '*.py')")


class Grep(Tool):
    name = "Grep"
    description = "Search file contents using a regex pattern, returning file:line:content matches."
    params_model = Params
    category = "read"
    is_concurrency_safe = True


    async def execute(self, params: Params) -> ToolResult:
        base = resolve_workspace_path(params.path)
        if not base.exists():
            return ToolResult(output=f"Error: path not found: {params.path}", is_error=True)

        try:
            regex = re.compile(params.pattern)
        except re.error as e:
            return ToolResult(output=f"Error: invalid regex: {e}", is_error=True)

        glob_pattern = params.include if params.include else "**/*"
        if not glob_pattern.startswith("**/"):
            glob_pattern = "**/" + glob_pattern

        results: list[str] = []
        result_chars = 0
        skipped = 0
        for file_path in base.glob(glob_pattern):
            if not file_path.resolve().is_relative_to(base) or not file_path.is_file():
                continue
            if any(part in SKIP_DIRS for part in file_path.parts):
                continue
            try:
                text = read_bounded_text(file_path, errors="ignore")
            except (OSError, UnicodeDecodeError):
                skipped += 1
                continue
            for line_num, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = file_path.relative_to(base)
                    match = f"{rel}:{line_num}:{line}"
                    remaining = MAX_OUTPUT_CHARS - result_chars
                    results.append(match[:remaining])
                    result_chars += len(match) + 1
                    if result_chars >= MAX_OUTPUT_CHARS:
                        return ToolResult(output="\n".join(results) + "\n[Search output truncated; narrow the path or pattern.]")

        if skipped:
            results.append(f"[Skipped {skipped} file(s) exceeding the read limit or unavailable for reading.]")
        if not results:
            return ToolResult(output="No matches found.")
        return ToolResult(output="\n".join(results))
