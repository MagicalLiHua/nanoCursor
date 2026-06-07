"""Small Git command boundary for backend services.

This module centralizes short `git` subprocess calls so service code does not
grow ad-hoc timeout and error handling at every call site.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path


GitCompletedProcess = subprocess.CompletedProcess[str]


def run_git(
    workspace: str | Path,
    args: list[str] | tuple[str, ...],
    *,
    timeout_seconds: int | float = 10,
) -> GitCompletedProcess:
    """Run `git -C <workspace> ...` and always return a CompletedProcess."""
    command = ["git", "-C", str(Path(workspace).resolve()), *[str(arg) for arg in args]]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            -1,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=f"git command timed out after {timeout_seconds}s",
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, -1, stdout="", stderr=str(exc))


async def run_git_async(
    workspace: str | Path,
    args: list[str] | tuple[str, ...],
    *,
    timeout_seconds: int | float = 10,
) -> GitCompletedProcess:
    """Async boundary for services that need git data from async routes."""
    return await asyncio.to_thread(
        run_git,
        workspace,
        args,
        timeout_seconds=timeout_seconds,
    )
