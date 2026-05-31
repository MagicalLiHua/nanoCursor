"""
Git integration tools for nanoCursor nanoCursor.

Provides:
- git_status: Working tree status
- git_diff: Show unstaged/staged changes
- git_commit: Commit changes with message
- git_log: Show recent commit history
- git_reset: Reset to a previous commit (soft/hard)
- git_init: Initialize git repo if needed

Every file change is automatically tracked. Agents can commit after
successful changes and rollback on failure.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional


def _run_git(args: list[str], cwd: Path, timeout: int = 30) -> tuple[int, str, str]:
    """Run a git command and return (exit_code, stdout, stderr)."""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout,
        )
        try:
            stdout = r.stdout.decode('utf-8', errors='replace')
            stderr = r.stderr.decode('utf-8', errors='replace')
        except Exception:
            stdout = str(r.stdout) if r.stdout else ""
            stderr = str(r.stderr) if r.stderr else ""
        return r.returncode, stdout.strip(), stderr.strip()
    except FileNotFoundError:
        return -1, "", "Git is not installed or not in PATH"
    except subprocess.TimeoutExpired:
        return -1, "", f"Git command timed out after {timeout}s"
    except Exception as e:
        return -1, "", f"Git error: {e}"


def ensure_git_repo(workspace: Path) -> str:
    """Ensure the workspace is a git repository. Init if needed."""
    git_dir = workspace / ".git"
    if git_dir.exists():
        code, _, _ = _run_git(["rev-parse", "--git-dir"], workspace)
        if code == 0:
            return "ok"
    # Initialize
    code, stdout, stderr = _run_git(["init"], workspace)
    if code != 0:
        return f"Error: Failed to init git repo: {stderr}"
    # Configure user if not set
    _run_git(["config", "user.name", "nanoCursor nanoCursor"], workspace)
    _run_git(["config", "user.email", "agenthub@nanocursor.local"], workspace)
    # Create .gitignore if it doesn't exist
    gitignore = workspace / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# nanoCursor workspace\n.nanocursor/\n.backups/\n.snapshots/\n"
            ".team/\n.tasks/\n.todos.json\n__pycache__/\n*.pyc\n"
            ".env\nnode_modules/\n.DS_Store\n",
            encoding="utf-8"
        )
        _run_git(["add", ".gitignore"], workspace)
        _run_git(["commit", "-m", "Initial commit (nanoCursor nanoCursor)"], workspace)
    return f"Git repo initialized at {workspace}"


def git_status(workspace: Path) -> str:
    """Show the working tree status."""
    result = ensure_git_repo(workspace)
    if result != "ok" and result.startswith("Error"):
        return result

    code, stdout, stderr = _run_git(["status", "--short"], workspace)
    if code != 0:
        return f"Error: {stderr or 'git status failed'}"

    if not stdout:
        return "Working tree clean. No changes."

    lines = ["## Git Status"]
    for line in stdout.splitlines():
        status_code = line[:2]
        filepath = line[3:]
        if status_code.startswith("M"):
            lines.append(f"  modified: {filepath}")
        elif status_code.startswith("A"):
            lines.append(f"  added:    {filepath}")
        elif status_code.startswith("D"):
            lines.append(f"  deleted:  {filepath}")
        elif status_code.startswith("?"):
            lines.append(f"  untracked: {filepath}")
        elif status_code.startswith("R"):
            lines.append(f"  renamed:  {filepath}")
        else:
            lines.append(f"  {status_code} {filepath}")
    return "\n".join(lines)


def git_diff(workspace: Path, staged: bool = False) -> str:
    """Show working tree changes as unified diff."""
    result = ensure_git_repo(workspace)
    if result != "ok" and result.startswith("Error"):
        return result

    args = ["diff", "--unified=5"]
    if staged:
        args.append("--staged")

    code, stdout, stderr = _run_git(args, workspace)
    if code != 0:
        return f"Error: {stderr or 'git diff failed'}"

    if not stdout:
        return "No changes (working tree matches HEAD)."

    # Truncate if too large
    lines = stdout.splitlines()
    if len(lines) > 200:
        return "\n".join(lines[:200]) + f"\n... ({len(lines) - 200} more lines)"

    return stdout


def git_commit(workspace: Path, message: str) -> str:
    """Stage all changes and commit with the given message."""
    result = ensure_git_repo(workspace)
    if result != "ok" and result.startswith("Error"):
        return result

    if not message.strip():
        return "Error: Commit message is required"

    # Stage all changes
    code, _, stderr = _run_git(["add", "-A"], workspace)
    if code != 0:
        return f"Error staging changes: {stderr}"

    # Check if there's anything to commit
    code, stdout, _ = _run_git(["diff", "--cached", "--quiet"], workspace)
    if code == 0:
        return "Nothing to commit (working tree clean)."

    # Commit
    code, stdout, stderr = _run_git(
        ["commit", "-m", message],
        workspace,
    )
    if code != 0:
        return f"Error committing: {stderr}"

    # Show what was committed
    code, log_out, _ = _run_git(
        ["log", "-1", "--oneline", "--stat"],
        workspace,
    )
    return f"Committed successfully.\n{log_out}"


def git_log(workspace: Path, count: int = 10) -> str:
    """Show recent commit history."""
    result = ensure_git_repo(workspace)
    if result != "ok" and result.startswith("Error"):
        return result

    code, stdout, stderr = _run_git(
        ["log", f"-{min(count, 50)}", "--oneline", "--decorate"],
        workspace,
    )
    if code != 0:
        return f"Error: {stderr or 'git log failed'}"

    if not stdout:
        return "No commits yet."

    lines = ["## Recent Commits"]
    for line in stdout.splitlines():
        lines.append(f"  {line}")
    return "\n".join(lines)


def git_reset(workspace: Path, mode: str = "soft", ref: str = "HEAD~1", confirmed: bool = False) -> str:
    """
    Reset to a previous state.

    Modes:
    - soft: Undo commit but keep changes staged
    - mixed: Undo commit and unstage changes (default git behavior)
    - hard: Discard all changes entirely (DANGEROUS)

    Ref: commit hash, HEAD~N, or branch name
    """
    result = ensure_git_repo(workspace)
    if result != "ok" and result.startswith("Error"):
        return result

    if mode == "hard" and not confirmed:
        return (
            "WARNING: Hard reset will permanently discard changes.\n"
            "If you're sure, call git_reset with mode='hard' and confirmed=true.\n"
            "Consider using mode='soft' or 'mixed' first."
        )
    if mode == "hard":
        return git_reset_hard(workspace, ref)

    args = ["reset"]
    if mode == "soft":
        args.append("--soft")
    elif mode == "mixed":
        pass  # default
    else:
        return f"Error: Unknown reset mode '{mode}'. Use 'soft', 'mixed', or 'hard'."

    args.append(ref)

    code, stdout, stderr = _run_git(args, workspace)
    if code != 0:
        return f"Error: Reset failed: {stderr}"

    # Show current state
    _, status_out, _ = _run_git(["status", "--short"], workspace)
    return f"Reset to {ref} (mode={mode}).\nCurrent status:\n{status_out or '(clean)'}"


def git_reset_hard(workspace: Path, ref: str = "HEAD~1") -> str:
    """Hard reset to a previous state. DISCARDS all uncommitted changes."""
    result = ensure_git_repo(workspace)
    if result != "ok" and result.startswith("Error"):
        return result

    code, stdout, stderr = _run_git(["reset", "--hard", ref], workspace)
    if code != 0:
        return f"Error: Hard reset failed: {stderr}"

    return f"Hard reset to {ref}. All changes discarded."


def git_file_history(workspace: Path, filepath: str, count: int = 5) -> str:
    """Show commit history for a specific file."""
    result = ensure_git_repo(workspace)
    if result != "ok" and result.startswith("Error"):
        return result

    code, stdout, stderr = _run_git(
        ["log", f"-{min(count, 20)}", "--oneline", "--", filepath],
        workspace,
    )
    if code != 0:
        return f"Error: {stderr or 'git log failed'}"

    if not stdout:
        return f"No commits for {filepath}"

    lines = [f"## History for {filepath}"]
    for line in stdout.splitlines():
        lines.append(f"  {line}")
    return "\n".join(lines)


def auto_track_changes(workspace: Path, description: str = "") -> str:
    """
    Automatically stage and commit the current changes.
    Called after successful tool operations that modify files.
    Returns a summary of what was committed.
    """
    result = ensure_git_repo(workspace)
    if result != "ok" and result.startswith("Error"):
        return ""

    # Don't commit if nothing changed
    code, _, _ = _run_git(["diff", "--quiet"], workspace)
    if code == 0:
        return ""

    # Build automatic commit message
    # Get list of changed files
    _, changed_files, _ = _run_git(["diff", "--name-only"], workspace)
    if not changed_files:
        return ""

    files_summary = ", ".join(changed_files.splitlines()[:5])
    if description:
        msg = f"{description}\n\nChanged: {files_summary}"
    else:
        msg = f"Auto-commit: changes to {files_summary}"

    return git_commit(workspace, msg)


# ========== Tool definitions for agent ==========

GIT_TOOLS = [
    {"name": "git_status", "description": "Show working tree status (modified, added, deleted, untracked files).",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "git_diff", "description": "Show detailed changes as unified diff. Use before committing to review.",
     "input_schema": {"type": "object", "properties": {
         "staged": {"type": "boolean", "description": "Show staged changes instead of working tree"},
     }, "required": []}},
    {"name": "git_commit", "description": "Commit all current changes with a descriptive message.",
     "input_schema": {"type": "object", "properties": {
         "message": {"type": "string", "description": "Descriptive commit message explaining WHY the change was made"},
     }, "required": ["message"]}},
    {"name": "git_log", "description": "Show recent commit history.",
     "input_schema": {"type": "object", "properties": {
         "count": {"type": "integer", "description": "Number of commits to show (default 10)"},
     }, "required": []}},
    {"name": "git_reset", "description": "Undo commits. Use 'soft' to keep changes, 'mixed' to unstage. Hard reset requires confirmed=true and discards changes.",
     "input_schema": {"type": "object", "properties": {
         "mode": {"type": "string", "description": "soft | mixed | hard"},
         "ref": {"type": "string", "description": "Commit ref, e.g. HEAD~1 or commit hash"},
         "confirmed": {"type": "boolean", "description": "Required true for hard reset"},
     }, "required": []}},
    {"name": "git_file_history", "description": "Show commit history for a specific file.",
     "input_schema": {"type": "object", "properties": {
         "filepath": {"type": "string", "description": "Path to the file relative to workspace"},
         "count": {"type": "integer", "description": "Number of commits to show"},
     }, "required": ["filepath"]}},
]


# ========== Integration with engine ==========

_git_workspace: Optional[Path] = None


def get_git_workspace() -> Path:
    """Get the current git workspace directory."""
    global _git_workspace
    if _git_workspace is None:
        from src.infra.config import WORKSPACE_DIR
        _git_workspace = Path(WORKSPACE_DIR).resolve()
    return _git_workspace


def set_git_workspace(path: Path):
    """Set the git workspace directory."""
    global _git_workspace
    _git_workspace = Path(path).resolve()


# Tool handlers (for engine.py TOOL_HANDLERS)
def handle_git_status(**kw) -> str:
    return git_status(get_git_workspace())

def handle_git_diff(staged: bool = False, **kw) -> str:
    return git_diff(get_git_workspace(), staged=staged)

def handle_git_commit(message: str, **kw) -> str:
    return git_commit(get_git_workspace(), message)

def handle_git_log(count: int = 10, **kw) -> str:
    return git_log(get_git_workspace(), count=count)

def handle_git_reset(mode: str = "soft", ref: str = "HEAD~1", confirmed: bool = False, **kw) -> str:
    return git_reset(get_git_workspace(), mode=mode, ref=ref, confirmed=confirmed)

def handle_git_file_history(filepath: str, count: int = 5, **kw) -> str:
    return git_file_history(get_git_workspace(), filepath, count=count)


__all__ = [
    "git_status", "git_diff", "git_commit", "git_log", "git_reset",
    "git_reset_hard", "git_file_history", "ensure_git_repo",
    "auto_track_changes", "get_git_workspace", "set_git_workspace",
    "GIT_TOOLS", "handle_git_status", "handle_git_diff", "handle_git_commit",
    "handle_git_log", "handle_git_reset", "handle_git_file_history",
]
