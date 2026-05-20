"""Safe subprocess command runner for eval tasks.

Executes commands inside an eval workspace with a hard timeout,
output size limits, and dangerous-command blocking.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


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


def run_command(
    command: str,
    cwd: str | Path,
    timeout_seconds: int = 120,
    max_stdout_chars: int = 100_000,
    max_stderr_chars: int = 20_000,
) -> dict:
    """Run a shell command safely inside *cwd*.

    Returns a dict with keys:
      command, exit_code, stdout, stderr, duration_ms, timed_out
    """
    cwd = Path(cwd).resolve()
    if not cwd.is_dir():
        raise FileNotFoundError(f"工作目录不存在: {cwd}")

    blocked = _is_dangerous(command)
    if blocked:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Error: 危险命令被拦截 (匹配 '{blocked}')",
            "duration_ms": 0,
            "timed_out": False,
        }

    try:
        import time
        start = time.monotonic()
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout_seconds,
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

        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": stdout[:max_stdout_chars],
            "stderr": stderr[:max_stderr_chars],
            "duration_ms": round(elapsed * 1000),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Error: 命令超时 ({timeout_seconds}s)",
            "duration_ms": timeout_seconds * 1000,
            "timed_out": True,
        }
    except FileNotFoundError:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": "Error: 命令未找到，请确认已安装对应程序。",
            "duration_ms": 0,
            "timed_out": False,
        }
    except Exception as exc:
        return {
            "command": command,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Error: {exc}",
            "duration_ms": 0,
            "timed_out": False,
        }
