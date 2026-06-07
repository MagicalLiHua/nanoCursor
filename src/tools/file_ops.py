"""Unified file operations for agent and team tools."""

from __future__ import annotations

import difflib
import os
import shlex
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from src.runtime.command_runner import run_command
from src.runtime.runtime_feature_flags import (
    go_filetools_addr,
    go_filetools_enabled,
    go_filetools_fallback_enabled,
)
from src.tools.path_safety import safe_path

_LIST_DIRECTORY_HIDDEN_NAMES = {
    ".backups",
    ".git",
    ".mypy_cache",
    ".nanocursor",
    ".pytest_cache",
    ".ruff_cache",
    ".snapshots",
    ".task_outputs",
    ".tasks",
    ".team",
    ".tox",
    ".transcripts",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
_LIST_DIRECTORY_HIDDEN_SUFFIXES = {".pyc", ".pyo", ".pyd", ".class", ".o", ".so", ".dll", ".dylib"}
_FILETOOLS_BACKEND_EVENT: ContextVar[dict[str, Any] | None] = ContextVar(
    "nanocursor_filetools_backend_event",
    default=None,
)
_GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR: dict[str, float] = {}


def pop_filetools_backend_event() -> dict[str, Any] | None:
    """Return and clear the latest backend diagnostic for the current tool call."""
    event = _FILETOOLS_BACKEND_EVENT.get()
    _FILETOOLS_BACKEND_EVENT.set(None)
    return event


def _record_filetools_backend_event(event: dict[str, Any] | None) -> None:
    _FILETOOLS_BACKEND_EVENT.set(event)


def _go_filetools_failure_cooldown_seconds() -> float:
    raw = os.getenv("NANOCURSOR_GO_FILETOOLS_FAILURE_COOLDOWN_SECONDS", "10")
    try:
        return max(0.0, min(float(raw), 300.0))
    except ValueError:
        return 10.0


def _go_filetools_on_cooldown(address: str) -> bool:
    until = _GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR.get(address, 0.0)
    if until <= time.monotonic():
        _GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR.pop(address, None)
        return False
    return True


def _go_filetools_client(workdir: str | Path):
    if not go_filetools_enabled():
        return None
    from src.tools.filetools_client import FileToolsClient

    return FileToolsClient(str(Path(workdir).resolve()), server_addr=go_filetools_addr())


def _with_go_filetools(workdir: str | Path, operation: Callable, fallback: Callable):
    address = go_filetools_addr()
    if _go_filetools_on_cooldown(address):
        _record_filetools_backend_event({
            "backend": "python",
            "fallback": True,
            "from_backend": "go",
            "address": address,
            "reason": "go filetools is on temporary cooldown after a previous failure",
        })
        return fallback()
    client = _go_filetools_client(workdir)
    if client is None:
        return fallback()
    try:
        result = operation(client)
        _GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR.pop(address, None)
        _record_filetools_backend_event({
            "backend": "go",
            "fallback": False,
            "address": address,
        })
        return result
    except Exception as exc:
        if not go_filetools_fallback_enabled():
            _record_filetools_backend_event({
                "backend": "go",
                "fallback": False,
                "address": address,
                "error": str(exc),
            })
            raise
        cooldown = _go_filetools_failure_cooldown_seconds()
        if cooldown > 0:
            _GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR[address] = time.monotonic() + cooldown
        fallback_event = {
            "backend": "python",
            "fallback": True,
            "from_backend": "go",
            "address": address,
            "reason": str(exc),
            "cooldown_seconds": cooldown,
        }
        _record_filetools_backend_event(fallback_event)
        try:
            from src.infra.logging import get_logger

            get_logger().warning("go_filetools_fallback", extra=fallback_event)
        except Exception:
            pass
        return fallback()
    finally:
        try:
            client.close()
        except Exception:
            pass


def _should_hide_list_entry(path: Path) -> bool:
    return path.name in _LIST_DIRECTORY_HIDDEN_NAMES or path.suffix.lower() in _LIST_DIRECTORY_HIDDEN_SUFFIXES


def _normalize_go_read_content(path: str, content: str) -> str:
    start = f"--- Content of {path} ---\n"
    end = f"\n--- End of {path} ---"
    if content.startswith(start) and content.endswith(end):
        return content[len(start):-len(end)]
    return content


def _normalize_go_directory_listing(content: str) -> str:
    lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("[DIR]"):
            name = line.removeprefix("[DIR]").strip()
            if name:
                lines.append(f"{name}/")
        elif line.startswith("[FILE]"):
            name = line.removeprefix("[FILE]").strip()
            if name:
                lines.append(name)
    if lines:
        return "\n".join(lines)
    if "为空" in content:
        return "(empty directory)"
    return content


def _run_verify_command(args: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    command = shlex.join(args)
    result = run_command(
        command,
        cwd=cwd,
        timeout_seconds=timeout,
        max_stdout_chars=2000,
        max_stderr_chars=2000,
        permission_level="shell_safe",
    )
    output = str(result.get("stderr") or "") or str(result.get("stdout") or "")
    return int(result.get("exit_code") if result.get("exit_code") is not None else -1), output


def _is_missing_verify_tool(returncode: int, output: str) -> bool:
    lowered = output.lower()
    return returncode in {127, -1} and (
        "not found" in lowered
        or "command not found" in lowered
        or "not recognized" in lowered
        or "no such file" in lowered
    )


def run_read(path: str, workdir: str | Path, limit: int | None = None) -> str:
    """Read a file from the workspace.

    Args:
        path: Workspace-relative or absolute file path.
        workdir: The workspace root directory.
        limit: Optional max number of lines to return.

    Returns:
        File content string, or an error message.
    """
    def fallback() -> str:
        content = safe_path(path, workdir).read_text(encoding="utf-8")
        if limit:
            lines = content.splitlines()
            if len(lines) > limit:
                content = "\n".join(lines[:limit]) + f"\n... ({len(lines) - limit} more lines)"
        return content[:50000]

    try:
        if go_filetools_enabled() and limit is None:
            return _with_go_filetools(
                workdir,
                lambda client: _normalize_go_read_content(path, _run_async_client(client.read_file(path)))[:50000],
                fallback,
            )
        return fallback()
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
            returncode, err = _run_verify_command(["node", "--check", str(path)], path.parent, 10)
            if _is_missing_verify_tool(returncode, err):
                return ""
            if returncode != 0:
                return f"JavaScript syntax error in {path.name}: {err[:500]}"
        elif suffix == ".ts":
            returncode, err = _run_verify_command(["npx", "esbuild", str(path), "--format=esm"], path.parent, 15)
            if _is_missing_verify_tool(returncode, err):
                return ""
            if returncode != 0:
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
    def fallback() -> tuple[str, Path]:
        fp = safe_path(path, workdir)
        fp.parent.mkdir(parents=True, exist_ok=True)
        existed = fp.exists()
        fp.write_text(content, encoding="utf-8")
        result = f"{'Updated' if existed else 'Created'} {path} ({len(content)} bytes)"
        return result, fp

    try:
        if go_filetools_enabled():
            def write_via_go(client):
                existed = safe_path(path, workdir).exists()
                message = _run_async_client(client.write_file(
                    path,
                    content,
                    overwrite=True,
                    backup_existing=False,
                ))
                return f"{'Updated' if existed else 'Created'} {path} ({len(content)} bytes)\n{message}", safe_path(path, workdir)

            result, fp = _with_go_filetools(workdir, write_via_go, fallback)
        else:
            result, fp = fallback()
        verify_result = auto_verify_file(fp)
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
    def fallback() -> tuple[str, Path | None]:
        fp = safe_path(path, workdir)
        if not fp.exists():
            return f"Error: File not found: {path}", None
        content = fp.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)

        if start_line is not None and end_line is not None:
            if start_line < 1 or end_line > len(lines) or start_line > end_line:
                return f"Error: Invalid line range {start_line}-{end_line} (file has {len(lines)} lines)", None
            old_slice = "".join(lines[start_line - 1 : end_line])
            new_lines = new_text.splitlines(keepends=True)
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines[-1] += "\n"
            new_slice = "".join(new_lines)
            new_content = "".join(lines[: start_line - 1] + new_lines + lines[end_line:])
            loc = f"lines {start_line}-{end_line}"
        elif old_text:
            if old_text not in content:
                return "Error: Text not found in file. Use start_line/end_line for line-based edits, or verify the exact text.", None
            old_slice = old_text
            new_slice = new_text
            new_content = content.replace(old_text, new_text, 1)
            loc = "matched text"
        else:
            return "Error: Provide either (old_text, new_text) or (start_line, end_line, new_text)", None

        fp.write_text(new_content, encoding="utf-8")
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
        return result, fp

    def edit_via_go(client) -> tuple[str, Path | None]:
        fp = safe_path(path, workdir)
        if not fp.exists():
            return f"Error: File not found: {path}", None
        if start_line is not None and end_line is not None:
            result = _run_async_client(client.edit_file(
                path,
                start_line=start_line,
                end_line=end_line,
                new_text=new_text,
                match_mode="exact",
                create_backup=True,
            ))
        elif old_text:
            result = _run_async_client(client.edit_file(
                path,
                search_block=old_text,
                replace_block=new_text,
                match_mode="exact",
                create_backup=True,
            ))
        else:
            return "Error: Provide either (old_text, new_text) or (start_line, end_line, new_text)", None
        return result, fp

    try:
        if go_filetools_enabled():
            result, fp = _with_go_filetools(workdir, edit_via_go, fallback)
        else:
            result, fp = fallback()
        if fp is not None and not result.lower().startswith("error") and "修改失败" not in result:
            verify_result = auto_verify_file(fp)
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
    def fallback() -> str:
        target = safe_path(path or ".", workdir)
        if not target.exists():
            return f"Error: Path not found: {path}"
        if not target.is_dir():
            return f"Error: Not a directory: {path}"

        entries = []
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if _should_hide_list_entry(child):
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(f"{child.name}{suffix}")
        return "\n".join(entries) or "(empty directory)"

    try:
        if go_filetools_enabled():
            return _with_go_filetools(
                workdir,
                lambda client: _normalize_go_directory_listing(_run_async_client(client.list_directory(path or "."))),
                fallback,
            )
        return fallback()
    except Exception as e:
        return f"Error: {e}"


def _run_async_client(coro):
    """Run the async gRPC compatibility client from sync agent tools."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()
