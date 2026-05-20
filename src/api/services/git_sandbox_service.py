"""Git branch sandbox: isolate run changes on work branches."""

from __future__ import annotations

import subprocess
import json
from pathlib import Path
from typing import Any

from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_git(workspace: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command in the workspace. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return -1, "", str(exc)


def is_git_repo(workspace_dir: str | None = None) -> bool:
    workspace = _workspace(workspace_dir)
    rc, _, _ = _run_git(workspace, "rev-parse", "--git-dir")
    return rc == 0


def _branch_name(thread_id: str) -> str:
    suffix = thread_id.replace("/", "_").replace("\\", "_")[:40]
    return f"nanocursor/run-{suffix}"


def _meta_path(workspace: Path, thread_id: str) -> Path:
    root = workspace / ".nanocursor" / "git_sandbox"
    root.mkdir(parents=True, exist_ok=True)
    safe = thread_id.replace("/", "_").replace("\\", "_")
    return root / f"{safe}.json"


def _read_meta(workspace: Path, thread_id: str) -> dict[str, Any]:
    path = _meta_path(workspace, thread_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def prepare_git_branch(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Create a work branch for an isolated run."""
    workspace = _workspace(workspace_dir)
    if not is_git_repo(str(workspace)):
        return {"git_available": False, "thread_id": thread_id}

    # Record original branch
    rc, original_branch, _ = _run_git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        original_branch = "main"

    # Check dirty
    rc, dirty_out, _ = _run_git(workspace, "status", "--porcelain")
    is_dirty = bool(dirty_out)

    # Create branch
    branch = _branch_name(thread_id)
    rc_create, _, create_err = _run_git(workspace, "checkout", "-b", branch)
    if rc_create != 0:
        rc_checkout, _, checkout_err = _run_git(workspace, "checkout", branch)
        if rc_checkout != 0:
            return {
                "git_available": True,
                "ok": False,
                "thread_id": thread_id,
                "branch": branch,
                "message": create_err or checkout_err or "无法创建或切换工作分支",
            }

    meta = {
        "thread_id": thread_id,
        "branch": branch,
        "original_branch": original_branch,
        "was_dirty": is_dirty,
    }
    _meta_path(workspace, thread_id).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "ok": True,
        "git_available": True,
        "thread_id": thread_id,
        "branch": branch,
        "original_branch": original_branch,
        "was_dirty": is_dirty,
    }


def git_branch_status(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Return git status for a run's work branch."""
    workspace = _workspace(workspace_dir)
    if not is_git_repo(str(workspace)):
        return {"git_available": False}

    branch = _branch_name(thread_id)
    rc, current, _ = _run_git(workspace, "rev-parse", "--abbrev-ref", "HEAD")
    rc_dirty, dirty, _ = _run_git(workspace, "status", "--porcelain")
    rc_diff, diff, _ = _run_git(workspace, "diff", "--stat")

    return {
        "git_available": True,
        "thread_id": thread_id,
        "branch": branch,
        "current_branch": current if rc == 0 else "",
        "is_dirty": bool(dirty),
        "changed_files": dirty.split("\n") if dirty else [],
        "diff_summary": diff if rc_diff == 0 else "",
    }


def commit_branch(
    thread_id: str,
    message: str = "",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Commit all changes on the work branch."""
    workspace = _workspace(workspace_dir)
    if not is_git_repo(str(workspace)):
        return {"git_available": False, "ok": False}

    msg = message or f"nanocursor: run {thread_id[:8]} changes"
    _run_git(workspace, "add", "-A")
    rc, out, err = _run_git(workspace, "commit", "-m", msg)
    if rc != 0:
        return {"ok": False, "message": err or "nothing to commit"}

    rc_hash, commit_hash, _ = _run_git(workspace, "rev-parse", "HEAD")
    return {
        "ok": True,
        "thread_id": thread_id,
        "commit_hash": commit_hash if rc_hash == 0 else "",
        "message": msg,
    }


def discard_branch(
    thread_id: str,
    confirmed: bool = False,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Discard the work branch and return to original branch."""
    if not confirmed:
        raise ValueError("discard_branch 需要 confirmed=true 确认。")

    workspace = _workspace(workspace_dir)
    if not is_git_repo(str(workspace)):
        return {"git_available": False, "ok": False}

    meta = _read_meta(workspace, thread_id)
    branch = meta.get("branch") or _branch_name(thread_id)
    original_branch = meta.get("original_branch")

    target_branch = ""
    for candidate in [item for item in [original_branch, "main", "master"] if item]:
        rc, _, _ = _run_git(workspace, "rev-parse", "--verify", candidate)
        if rc == 0:
            target_branch = candidate
            break
    if not target_branch:
        return {"ok": False, "message": "找不到原始分支或 main/master 分支，无法切换回去。"}

    rc_checkout, _, checkout_err = _run_git(workspace, "checkout", target_branch)
    if rc_checkout != 0:
        return {"ok": False, "message": checkout_err or f"无法切换到 {target_branch}"}

    _run_git(workspace, "branch", "-D", branch)
    return {
        "ok": True,
        "thread_id": thread_id,
        "discarded_branch": branch,
        "restored_branch": target_branch,
    }
