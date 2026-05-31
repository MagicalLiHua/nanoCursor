"""Unified bash execution for agent and team tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

# Default timeout; callers can override via the timeout parameter.
DEFAULT_BASH_TIMEOUT = 120

DANGEROUS_COMMANDS = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]


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
        r = subprocess.run(command, shell=True, cwd=str(workdir), capture_output=True, timeout=timeout)
        try:
            out = r.stdout.decode("gbk", errors="replace") + r.stderr.decode("gbk", errors="replace")
        except (UnicodeDecodeError, AttributeError):
            out = (r.stdout or b"") + (r.stderr or b"")
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
        return out.strip()[:50000] or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Timeout ({timeout}s)"
    except FileNotFoundError:
        return f"Error: Command not found: {command.split()[0] if command else ''}"
    except Exception as e:
        return f"Error: {e}"
