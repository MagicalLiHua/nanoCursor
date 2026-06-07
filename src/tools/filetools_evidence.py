"""Normalize file tool outputs into structured evidence."""

from __future__ import annotations

import re
from typing import Literal, TypedDict

from src.tools.tool_result import is_tool_error_output, tool_error_message

FILE_TOOL_NAMES = {
    "read_file",
    "list_directory",
    "write_file",
    "edit_file",
    "backup_file",
    "rollback_file",
}


class FileToolEvidence(TypedDict):
    backend: Literal["python", "go", "unknown"]
    operation: str
    path: str
    changed: bool
    created: bool
    overwritten: bool
    backup_path: str | None
    diff: str | None
    verification: str | None
    error: str | None


def build_file_tool_evidence(
    tool_name: str,
    tool_input: dict | None,
    output: str | None,
) -> FileToolEvidence | None:
    """Build best-effort evidence from a file tool call.

    The runtime still stores the raw tool output. This adapter adds a stable
    shape for reports, event consumers, and future frontend panels without
    forcing every file tool to return a new object type.
    """
    if tool_name not in FILE_TOOL_NAMES:
        return None

    tool_input = tool_input or {}
    text = str(output or "")
    path = str(
        tool_input.get("path")
        or tool_input.get("file_path")
        or tool_input.get("filename")
        or ""
    )
    error = tool_error_message(text) if is_tool_error_output(text) else None
    operation = _operation_for_tool(tool_name)
    backend = _infer_backend(text)
    diff = _extract_diff(text)
    backup_path = _extract_backup_path(text)
    verification = _extract_verification(text)
    created = "Created " in text or "Successfully created file" in text
    overwritten = "Updated " in text or "Successfully updated file" in text
    changed = error is None and operation in {"write", "edit", "rollback"}

    return {
        "backend": backend,
        "operation": operation,
        "path": path,
        "changed": changed,
        "created": created,
        "overwritten": overwritten,
        "backup_path": backup_path,
        "diff": diff,
        "verification": verification,
        "error": error,
    }


def _operation_for_tool(tool_name: str) -> str:
    return {
        "read_file": "read",
        "list_directory": "list",
        "write_file": "write",
        "edit_file": "edit",
        "backup_file": "backup",
        "rollback_file": "rollback",
    }.get(tool_name, tool_name)


def _infer_backend(output: str) -> Literal["python", "go", "unknown"]:
    if "Successfully created file:" in output or "Successfully updated file:" in output:
        return "go"
    if "Edit Receipt:" in output or "使用策略:" in output:
        return "go"
    if output.startswith(("Created ", "Updated ", "Edited ")):
        return "python"
    if output.startswith(("--- Content of", "目录 '")):
        return "go"
    return "unknown"


def _extract_diff(output: str) -> str | None:
    match = re.search(r"```diff\n(?P<diff>.*?)\n```", output, flags=re.DOTALL)
    if not match:
        return None
    return match.group("diff")


def _extract_backup_path(output: str) -> str | None:
    patterns = [
        r"原文件已备份到 (?P<path>[^)]+)",
        r"backup:\s*(?P<path>[^)]+)",
        r"backup_path:\s*(?P<path>\S+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, output)
        if match:
            return match.group("path").strip()
    return None


def _extract_verification(output: str) -> str | None:
    marker = "⚠️"
    if marker not in output:
        return None
    return output.split(marker, 1)[1].strip() or None

