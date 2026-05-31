"""Unified file operations for agent and team tools."""

from __future__ import annotations

import difflib
import subprocess
from pathlib import Path

from src.tools.path_safety import safe_path


def run_read(path: str, workdir: str | Path, limit: int | None = None) -> str:
    """Read a file from the workspace.

    Args:
        path: Workspace-relative or absolute file path.
        workdir: The workspace root directory.
        limit: Optional max number of lines to return.

    Returns:
        File content string, or an error message.
    """
    try:
        content = safe_path(path, workdir).read_text(encoding="utf-8")
        if limit:
            lines = content.splitlines()
            if len(lines) > limit:
                content = "\n".join(lines[:limit]) + f"\n... ({len(lines) - limit} more lines)"
        return content[:50000]
    except Exception as e:
        return f"Error: {e}"


def auto_verify_file(path: Path) -> str:
    """Verify a file after writing/editing. Returns '' if OK, error message if not."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".py":
            import py_compile
            try:
                py_compile.compile(str(path), doraise=True)
            except py_compile.PyCompileError as e:
                return f"Python syntax error in {path.name}: {e}"
        elif suffix in (".js", ".mjs"):
            r = subprocess.run(
                ["node", "--check", str(path)],
                capture_output=True, timeout=10,
            )
            if r.returncode != 0:
                err = r.stderr or r.stdout
                if isinstance(err, bytes):
                    err = err.decode("utf-8", errors="replace")
                return f"JavaScript syntax error in {path.name}: {err[:500]}"
        elif suffix == ".ts":
            r = subprocess.run(
                ["npx", "esbuild", str(path), "--format=esm"],
                capture_output=True, timeout=15,
            )
            if r.returncode != 0:
                err = r.stderr or r.stdout
                if isinstance(err, bytes):
                    err = err.decode("utf-8", errors="replace")
                return f"TypeScript error in {path.name}: {err[:500]}"
        elif suffix in (".json",):
            import json
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                return f"JSON syntax error in {path.name}: {e}"
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return ""


def run_write(path: str, content: str, workdir: str | Path) -> str:
    """Write content to a file in the workspace.

    Args:
        path: Workspace-relative or absolute file path.
        content: Text content to write.
        workdir: The workspace root directory.

    Returns:
        Confirmation message, or an error message.
    """
    try:
        fp = safe_path(path, workdir)
        fp.parent.mkdir(parents=True, exist_ok=True)
        existed = fp.exists()
        fp.write_text(content, encoding="utf-8")
        verify_result = auto_verify_file(fp)
        result = f"{'Updated' if existed else 'Created'} {path} ({len(content)} bytes)"
        if verify_result:
            result += f"\n⚠️  {verify_result}"
        return result
    except Exception as e:
        return f"Error: {e}"


def run_edit(
    path: str,
    workdir: str | Path,
    old_text: str = "",
    new_text: str = "",
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Edit a file in the workspace.

    Supports two modes:
    - Line-based (preferred): provide start_line, end_line, new_text (1-indexed).
    - String-based (legacy): provide old_text, new_text.

    Returns a diff summary of what changed, or an error message.
    """
    try:
        fp = safe_path(path, workdir)
        if not fp.exists():
            return f"Error: File not found: {path}"
        content = fp.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        if start_line is not None and end_line is not None:
            if start_line < 1 or end_line > len(lines) or start_line > end_line:
                return f"Error: Invalid line range {start_line}-{end_line} (file has {len(lines)} lines)"
            old_slice = "".join(lines[start_line - 1 : end_line])
            new_lines = new_text.splitlines(keepends=True)
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines[-1] += "\n"
            new_slice = "".join(new_lines)
            new_content = "".join(lines[: start_line - 1] + new_lines + lines[end_line:])
            loc = f"lines {start_line}-{end_line}"
        elif old_text:
            if old_text not in content:
                return "Error: Text not found in file. Use start_line/end_line for line-based edits, or verify the exact text."
            old_slice = old_text
            new_slice = new_text
            new_content = content.replace(old_text, new_text, 1)
            loc = "matched text"
        else:
            return "Error: Provide either (old_text, new_text) or (start_line, end_line, new_text)"

        fp.write_text(new_content, encoding="utf-8")
        verify_result = auto_verify_file(fp)

        diff_lines = list(difflib.unified_diff(
            old_slice.splitlines(keepends=True),
            new_slice.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        ))
        diff_text = "\n".join(diff_lines[:30])
        added = len(new_slice) - len(old_slice)
        change = f"+{added}" if added >= 0 else str(added)
        result = f"Edited {path} ({loc}, {change} chars)\n```diff\n{diff_text}\n```"
        if verify_result:
            result += f"\n⚠️  {verify_result}"
        return result
    except Exception as e:
        return f"Error: {e}"


def run_list_directory(path: str, workdir: str | Path) -> str:
    """List a workspace-relative directory.

    Args:
        path: Workspace-relative directory path.
        workdir: The workspace root directory.

    Returns:
        Newline-separated listing, or an error message.
    """
    try:
        target = safe_path(path or ".", workdir)
        if not target.exists():
            return f"Error: Path not found: {path}"
        if not target.is_dir():
            return f"Error: Not a directory: {path}"

        entries = []
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            suffix = "/" if child.is_dir() else ""
            entries.append(f"{child.name}{suffix}")
        return "\n".join(entries) or "(empty directory)"
    except Exception as e:
        return f"Error: {e}"
