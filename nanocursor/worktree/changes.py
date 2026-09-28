from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_OPTIONAL_LOCKS": "0"}


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    import os
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


@dataclass
class Changes:
    uncommitted: int = 0
    new_commits: int = 0
    ignored: int = 0
    state: str = "clean"
    error: str = ""


def count_worktree_changes(wt_path: str, head_commit: str) -> Changes:
    changes = Changes()
    try:
        if not head_commit:
            raise ValueError("Creation baseline is missing")
        status = _run_git(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored"], cwd=wt_path
        )
        if status.returncode != 0:
            raise ValueError("git status failed: " + status.stderr.strip())
        entries = iter(status.stdout.split("\0"))
        for entry in entries:
            if not entry:
                continue
            if len(entry) < 4 or entry[2] != " ":
                raise ValueError("Cannot parse git status")
            if entry.startswith("!! "):
                changes.ignored += 1
            else:
                changes.uncommitted += 1
            if entry[0] in "RC" or entry[1] in "RC":
                if not next(entries, ""):
                    raise ValueError("Incomplete rename in git status")
        rev_list = _run_git(
            ["rev-list", "--count", f"{head_commit}..HEAD"], cwd=wt_path
        )
        if rev_list.returncode != 0:
            raise ValueError("Cannot query the creation baseline: " + rev_list.stderr.strip())
        changes.new_commits = int(rev_list.stdout.strip())
        if changes.new_commits < 0:
            raise ValueError("Invalid commit count")
        head = _run_git(["rev-parse", "HEAD"], cwd=wt_path)
        if head.returncode != 0:
            raise ValueError("Cannot query worktree HEAD")
        changes.state = "changed" if (
            changes.uncommitted or changes.new_commits or changes.ignored
            or head.stdout.strip() != head_commit
        ) else "clean"
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        changes.state = "unknown"
        changes.error = str(exc)
    return changes


def has_worktree_changes(wt_path: str, head_commit: str) -> bool:
    c = count_worktree_changes(wt_path, head_commit)
    return c.state != "clean"


@dataclass
class CleanupResult:
    kept: bool
    path: str = ""
    branch: str = ""


def has_unpushed_commits(wt_path: str) -> bool:
    try:
        result = _run_git(
            ["rev-list", "--max-count=1", "HEAD", "--not", "--remotes"],
            cwd=wt_path,
        )
        return bool(result.stdout.strip()) if result.returncode == 0 else True
    except (subprocess.SubprocessError, OSError):
        return True
