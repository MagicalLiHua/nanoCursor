"""Unified bash execution for agent and team tools."""

from __future__ import annotations

import os
import re
from pathlib import Path

from src.runtime.command_runner import run_command

# Default timeout; callers can override via the timeout parameter.
DEFAULT_BASH_TIMEOUT = 120

DANGEROUS_COMMANDS = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]

_PYTHON_TEST_COMMAND_RE = re.compile(
    r"(^|[;&|]\s*)"
    r"("
    r"python(?:\d+(?:\.\d+)?)?\s+-m\s+(pytest|unittest)"
    r"|pytest(\s|$)"
    r")",
    re.IGNORECASE,
)


def _python_test_env(command: str, workdir: Path) -> dict[str, str] | None:
    """Return an env that makes src-layout Python tests importable.

    Agents sometimes call pytest through the generic bash tool instead of the
    dedicated run_tests tool. For local src/ layouts, pytest needs the source
    directory on PYTHONPATH unless the package has already been installed.
    """
    src_dir = workdir / "src"
    if "PYTHONPATH" in command or not src_dir.is_dir():
        return None
    if not _PYTHON_TEST_COMMAND_RE.search(command):
        return None
    env = os.environ.copy()
    src_path = str(src_dir.resolve())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_path if not existing else f"{src_path}{os.pathsep}{existing}"
    return env


def run_bash(command: str, workdir: str | Path, timeout: int = DEFAULT_BASH_TIMEOUT) -> str:
    """Execute a shell command in the given working directory.

    Args:
        command: Shell command string.
        workdir: Working directory for command execution.
        timeout: Timeout in seconds (default 120).

    Returns:
        Combined stdout+stderr string, or an error message.
    """
    if any(d in command for d in DANGEROUS_COMMANDS):
        return "Error: Dangerous command blocked"
    try:
        resolved_workdir = Path(workdir).resolve()
        result = run_command(
            command,
            cwd=str(resolved_workdir),
            timeout_seconds=timeout,
            max_stdout_chars=50000,
            max_stderr_chars=50000,
            permission_level="shell_safe",
            env=_python_test_env(command, resolved_workdir),
        )
        if result.get("timed_out"):
            return f"Error: Timeout ({timeout}s)"
        out = str(result.get("stdout") or "") + str(result.get("stderr") or "")
        return out.strip()[:50000] or "(no output)"
    except FileNotFoundError:
        return f"Error: Command not found: {command.split()[0] if command else ''}"
    except Exception as e:
        return f"Error: {e}"
