"""Safe subprocess command runner for eval tasks.

Executes commands inside an eval workspace with a hard timeout,
output size limits, and dangerous-command blocking.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any, Callable

try:
    from src.runtime import executor_client
    _EXECUTOR_AVAILABLE = True
except ImportError:
    _EXECUTOR_AVAILABLE = False

from src.runtime.executor_routing import choose_executor_backend
from src.runtime.go_runtime_client import GoRuntimeUnavailable, run_command_via_go_runtime
from src.runtime.runtime_feature_flags import (
    go_executor_enabled,
    go_executor_fallback_enabled,
    go_runtime_enabled,
    go_runtime_fallback_enabled,
)


# Commands that are never allowed in eval
_BLOCKED_PATTERNS = [
    "rm -rf /", "sudo ", "shutdown", "reboot", "mkfs", "chroot",
    "dd if=", "> /dev/sda", "format c:", "del /f /s",
]


def _is_dangerous(command: str) -> str | None:
    """Return the blocked pattern if *command* matches one, else None."""
    cmd_lower = command.lower()
    for pattern in _BLOCKED_PATTERNS:
        if pattern.lower() in cmd_lower:
            return pattern
    return None


def _base_result(
    *,
    backend: str,
    route_reason: str,
    fallback: bool,
    command: str,
    cwd: Path,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
    duration_ms: int | float = 0,
    timed_out: bool = False,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "route_reason": route_reason,
        "fallback": fallback,
        "command": command,
        "cwd": str(cwd),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "duration_ms": duration_ms,
        "timed_out": timed_out,
    }


def _emit_command_backend_decision(
    callback: Callable[[dict[str, Any]], None] | None,
    command: str,
    decision: dict[str, Any],
    permission_level: str,
) -> None:
    if callback is None:
        return
    try:
        callback({
            "type": "command_backend_decision",
            "command": command,
            "permission_level": permission_level,
            **decision,
        })
    except Exception:
        pass


def _emit_command_backend_fallback(
    callback: Callable[[dict[str, Any]], None] | None,
    command: str,
    *,
    from_backend: str,
    to_backend: str,
    reason: str,
) -> None:
    if callback is None:
        return
    try:
        callback({
            "type": "command_backend_fallback",
            "command": command,
            "from_backend": from_backend,
            "to_backend": to_backend,
            "reason": reason,
        })
    except Exception:
        pass


def run_command(
    command: str,
    cwd: str | Path,
    timeout_seconds: int = 120,
    max_stdout_chars: int = 100_000,
    max_stderr_chars: int = 20_000,
    permission_level: str = "shell_safe",
    approval_id: str | None = None,
    approval_token: str | None = None,
    thread_id: str | None = None,
    on_runtime_event: Callable[[dict[str, Any]], None] | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    """Run a shell command safely inside *cwd*.

    Returns a dict with keys:
      command, exit_code, stdout, stderr, duration_ms, timed_out
    """
    cwd = Path(cwd).resolve()
    if not cwd.is_dir():
        raise FileNotFoundError(f"工作目录不存在: {cwd}")
    backend = "python_subprocess"
    route_reason = "default to Python subprocess"
    fallback = False

    blocked = _is_dangerous(command)
    if blocked:
        return _base_result(
            backend=backend,
            route_reason="dangerous command blocked before backend routing",
            fallback=False,
            command=command,
            cwd=cwd,
            exit_code=-1,
            stderr=f"Error: 危险命令被拦截 (匹配 '{blocked}')",
        )

    decision = choose_executor_backend(
        command=command,
        timeout_seconds=timeout_seconds,
        permission_level=permission_level or "shell_safe",
        requires_streaming=on_runtime_event is not None,
        env=env,
    )
    route_reason = decision.reason
    _emit_command_backend_decision(on_runtime_event, command, decision.to_dict(), permission_level)

    if decision.backend == "go_executor" and not _EXECUTOR_AVAILABLE:
        if not go_executor_fallback_enabled():
            raise RuntimeError("Go executor selected but executor client is unavailable")
        fallback = True
        route_reason = f"{decision.reason}; executor client unavailable, used Python fallback"
        _emit_command_backend_fallback(
            on_runtime_event,
            command,
            from_backend="go_executor",
            to_backend="python_subprocess",
            reason="executor client unavailable",
        )

    if _EXECUTOR_AVAILABLE and decision.backend == "go_executor":
        try:
            result = executor_client.run_command(
                command,
                cwd=str(cwd),
                workspace_dir=str(cwd),
                timeout_ms=int((timeout_seconds or 120) * 1000),
                permission_level=permission_level or "shell_safe",
                on_event=on_runtime_event,
            )
            result.setdefault("backend", "go_executor")
            result["route_reason"] = decision.reason
            result["fallback"] = False
            return result
        except Exception as exc:
            if not go_executor_fallback_enabled():
                raise
            fallback = True
            route_reason = f"{decision.reason}; Go executor failed, used Python fallback: {exc}"
            _emit_command_backend_fallback(
                on_runtime_event,
                command,
                from_backend="go_executor",
                to_backend="python_subprocess",
                reason=str(exc),
            )

    # Try go-runtime HTTP (secondary backend)
    if go_runtime_enabled() and not go_executor_enabled() and env is None:
        try:
            result = run_command_via_go_runtime(
                command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_stdout_chars=max_stdout_chars,
                max_stderr_chars=max_stderr_chars,
                permission_level=permission_level,
                approval_id=approval_id,
                approval_token=approval_token,
                run_id=thread_id,
                on_runtime_event=on_runtime_event,
            )
            result.setdefault("backend", "go_runtime")
            result.setdefault("route_reason", "legacy Go runtime enabled")
            result.setdefault("fallback", False)
            return result
        except GoRuntimeUnavailable:
            if not go_runtime_fallback_enabled():
                raise

    try:
        import time
        start = time.monotonic()
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout_seconds,
            env=env,
        )
        elapsed = time.monotonic() - start

        try:
            stdout = result.stdout.decode("utf-8", errors="replace")
        except Exception:
            stdout = ""
        try:
            stderr = result.stderr.decode("utf-8", errors="replace")
        except Exception:
            stderr = ""

        stdout_truncated = len(stdout) > max_stdout_chars
        stderr_truncated = len(stderr) > max_stderr_chars
        return {
            "backend": backend,
            "route_reason": route_reason,
            "fallback": fallback,
            "command": command,
            "cwd": str(cwd),
            "exit_code": result.returncode,
            "stdout": stdout[:max_stdout_chars],
            "stderr": stderr[:max_stderr_chars],
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": round(elapsed * 1000),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "backend": backend,
            "route_reason": route_reason,
            "fallback": fallback,
            "command": command,
            "cwd": str(cwd),
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Error: 命令超时 ({timeout_seconds}s)",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "duration_ms": timeout_seconds * 1000,
            "timed_out": True,
        }
    except FileNotFoundError:
        return {
            "backend": backend,
            "route_reason": route_reason,
            "fallback": fallback,
            "command": command,
            "cwd": str(cwd),
            "exit_code": -1,
            "stdout": "",
            "stderr": "Error: 命令未找到，请确认已安装对应程序。",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "duration_ms": 0,
            "timed_out": False,
        }
    except Exception as exc:
        return {
            "backend": backend,
            "route_reason": route_reason,
            "fallback": fallback,
            "command": command,
            "cwd": str(cwd),
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Error: {exc}",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "duration_ms": 0,
            "timed_out": False,
        }


async def run_command_async(
    command: str,
    cwd: str | Path,
    timeout_seconds: int = 120,
    max_stdout_chars: int = 100_000,
    max_stderr_chars: int = 20_000,
    permission_level: str = "shell_safe",
    approval_id: str | None = None,
    approval_token: str | None = None,
    thread_id: str | None = None,
    on_runtime_event: Callable[[dict[str, Any]], None] | None = None,
    env: dict[str, str] | None = None,
) -> dict:
    """Async boundary for command execution.

    The command runner intentionally remains sync for older service and test
    callers. Async API routes should call this wrapper so subprocess execution
    and the synchronous Go-runtime adapter do not block the event loop.
    """
    return await asyncio.to_thread(
        run_command,
        command=command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        max_stdout_chars=max_stdout_chars,
        max_stderr_chars=max_stderr_chars,
        permission_level=permission_level,
        approval_id=approval_id,
        approval_token=approval_token,
        thread_id=thread_id,
        on_runtime_event=on_runtime_event,
        env=env,
    )
