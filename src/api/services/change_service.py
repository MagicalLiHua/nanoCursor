"""Change set service — collect, review, and approve file changes for a run.

Uses git when available; falls back to file-snapshot comparison for non-git projects.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.infra.path_guard import resolve_workspace_path
from src.runtime.change_set import ChangeSet, ChangeSetStatus, FilePatchSummary


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_dir(thread_id: str, workspace_dir: str | None = None) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return _workspace(workspace_dir) / ".nanocursor" / "runs" / safe_id


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _run_git(workspace: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True, text=True, timeout=15, check=False,
    )


def _is_git_repo(workspace: Path) -> bool:
    result = _run_git(workspace, ["rev-parse", "--git-dir"])
    return result.returncode == 0


def _classify_risk(path: str, change_type: str, additions: int, deletions: int) -> str:
    path_lower = path.lower()

    if change_type == "deleted":
        return "high"
    if additions + deletions > 500:
        return "high"
    if any(path_lower.endswith(ext) for ext in (".lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml")):
        return "high"
    if any(seg in path_lower for seg in (".env", "secret", "credential", "key", "token")):
        return "high"
    if any(path_lower.startswith(prefix) for prefix in (".github/workflows", ".gitlab-ci", "jenkinsfile", "dockerfile")):
        return "medium"
    if path_lower.endswith((".conf", ".cfg", ".ini", ".toml", ".yaml", ".yml")):
        return "medium"
    if any(seg in path_lower for seg in ("test_", "_test.", "tests/", "__test__", "spec/")):
        return "low"
    return "medium"


def _parse_numstat(output: str) -> dict[str, tuple[int, int]]:
    """Parse 'git diff --numstat' output into {path: (additions, deletions)}."""
    result: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            try:
                adds = int(parts[0]) if parts[0] != "-" else 0
                dels = int(parts[1]) if parts[1] != "-" else 0
                result[parts[2]] = (adds, dels)
            except ValueError:
                continue
    return result


def _parse_status(output: str) -> dict[str, str]:
    """Parse 'git status --porcelain' output into {path: change_type}."""
    result: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        raw_status = line[:2]
        status = raw_status.strip()
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        status_chars = set(raw_status)
        if status == "??" or "A" in status_chars or "C" in status_chars:
            change_type = "added"
        elif "R" in status_chars:
            change_type = "renamed"
        elif "D" in status_chars:
            change_type = "deleted"
        else:
            change_type = "modified"
        result[path] = change_type
    return result


def _is_internal_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    return normalized == ".nanocursor" or normalized.startswith((".nanocursor/", ".backups/", ".tasks/", ".snapshots/"))


def _safe_workspace_file(workspace: Path, path: str) -> Path | None:
    if not path or path.startswith(("/", "\\")):
        return None
    candidate = (workspace / path).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _expand_untracked_directories(workspace: Path, status_map: dict[str, str]) -> dict[str, str]:
    expanded: dict[str, str] = {}
    for path, change_type in status_map.items():
        if change_type != "added" or not path.endswith("/"):
            expanded[path] = change_type
            continue

        directory = (workspace / path).resolve()
        try:
            directory.relative_to(workspace)
        except ValueError:
            expanded[path] = change_type
            continue
        if not directory.is_dir():
            expanded[path] = change_type
            continue

        found = False
        for child in sorted(directory.rglob("*")):
            if not child.is_file():
                continue
            relative = child.relative_to(workspace).as_posix()
            if _is_internal_path(relative) or "/.git/" in f"/{relative}/":
                continue
            expanded[relative] = "added"
            found = True
        if not found:
            expanded[path] = change_type
    return expanded


def _line_count(workspace: Path, path: str) -> int:
    file_path = _safe_workspace_file(workspace, path)
    if file_path is None:
        return 0
    try:
        return len(file_path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _parse_diff_unified_zero(output: str) -> dict[str, int]:
    """Parse 'git diff --unified=0' to count hunks per file."""
    hunks: dict[str, int] = {}
    current_file = ""
    for line in output.splitlines():
        if line.startswith("diff --git"):
            current_file = ""
        elif line.startswith("+++ b/"):
            current_file = line[6:].strip()
        elif line.startswith("@@") and current_file:
            hunks[current_file] = hunks.get(current_file, 0) + 1
    return hunks


def collect_changes_git(workspace: Path, include_untracked: bool = True) -> list[FilePatchSummary]:
    """Collect file changes using git."""
    numstat_result = _run_git(workspace, ["diff", "--numstat", "--find-renames", "--", "."])
    status_result = _run_git(workspace, ["status", "--porcelain", "--", "."])

    if include_untracked:
        numstat_all = _run_git(workspace, ["diff", "--numstat", "--find-renames", "HEAD", "--", "."])
        if numstat_all.returncode == 0 and numstat_all.stdout.strip():
            numstat_result = numstat_all

    numstat = _parse_numstat(numstat_result.stdout) if numstat_result.returncode == 0 else {}
    status_map = _parse_status(status_result.stdout) if status_result.returncode == 0 else {}
    status_map = _expand_untracked_directories(workspace, status_map)
    status_map = {path: change_type for path, change_type in status_map.items() if not _is_internal_path(path)}
    numstat = {path: stats for path, stats in numstat.items() if not _is_internal_path(path)}

    all_files = set(numstat.keys()) | set(status_map.keys())
    files: list[FilePatchSummary] = []

    for path in sorted(all_files):
        adds, dels = numstat.get(path, (0, 0))
        ct = status_map.get(path, "modified")
        if ct == "added" and adds == 0 and dels == 0:
            adds = _line_count(workspace, path)
        risk = _classify_risk(path, ct, adds, dels)

        files.append(FilePatchSummary(
            path=path,
            change_type=ct,
            additions=adds,
            deletions=dels,
            hunks=0,  # computed below
            summary="",
            risk=risk,
        ))

    # Add hunk counts
    diff_unified = _run_git(workspace, ["diff", "--unified=0", "--find-renames", "HEAD", "--", "."])
    if diff_unified.returncode == 0:
        hunk_map = _parse_diff_unified_zero(diff_unified.stdout)
        for f in files:
            f.hunks = hunk_map.get(f.path, 0)

    return files


def collect_changes_fallback(workspace: Path, thread_id: str) -> list[FilePatchSummary]:
    """Fallback: compare file snapshots from checkpoints or list modified files."""
    from src.api.services.checkpoint_service import list_checkpoints

    cp_info = list_checkpoints(thread_id, str(workspace))
    files_by_path = cp_info.get("files", {})

    if not files_by_path:
        return []

    result: list[FilePatchSummary] = []
    for filepath, checkpoints in files_by_path.items():
        change_type = "modified"
        risk = _classify_risk(filepath, change_type, 0, 0)
        result.append(FilePatchSummary(
            path=filepath,
            change_type=change_type,
            additions=0,
            deletions=0,
            hunks=len(checkpoints),
            summary="",
            risk=risk,
        ))
    return result


def collect_changes_from_events(workspace: Path, thread_id: str) -> list[FilePatchSummary]:
    """Collect file changes from file_changed events recorded during a run."""
    from src.api.services.event_store import get_event_store

    files_by_path: dict[str, FilePatchSummary] = {}
    for event in get_event_store().list_events(thread_id, str(workspace)):
        if event.type != "file_changed" or not isinstance(event.payload, dict):
            continue

        filepath = str(event.payload.get("path") or "").strip()
        if not filepath:
            continue

        output = str(event.payload.get("output") or event.content or "")
        created = output.startswith("Created ")
        change_type = "added" if created else str(event.payload.get("change_type") or "modified")

        additions = 0
        resolved = resolve_workspace_path(workspace, filepath)
        if change_type in {"added", "created"} and resolved.exists() and resolved.is_file():
            try:
                additions = len(resolved.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                additions = 0

        files_by_path[filepath] = FilePatchSummary(
            path=filepath,
            change_type="added" if change_type == "created" else change_type,
            additions=additions,
            deletions=0,
            hunks=1,
            summary=output[:240],
            risk=_classify_risk(filepath, change_type, additions, 0),
        )

    return [files_by_path[path] for path in sorted(files_by_path)]


def collect_changes(thread_id: str, workspace_dir: str | None = None, include_untracked: bool = True) -> ChangeSet:
    """Collect file changes for a run. Uses git when available."""
    ws = _workspace(workspace_dir)
    ws_str = str(ws)

    if _is_git_repo(ws):
        files = collect_changes_git(ws, include_untracked=include_untracked)
    else:
        files = collect_changes_fallback(ws, thread_id)
    if not files:
        files = collect_changes_from_events(ws, thread_id)

    total_adds = sum(f.additions for f in files)
    total_dels = sum(f.deletions for f in files)

    cs = ChangeSet(
        thread_id=thread_id,
        workspace_dir=ws_str,
        base_ref="HEAD",
        status=ChangeSetStatus.COLLECTED,
        files=files,
        total_additions=total_adds,
        total_deletions=total_dels,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    save_change_set(cs)
    return cs


def review_changes(thread_id: str, workspace_dir: str | None = None) -> ChangeSet:
    """Run rule-based risk review on collected changes. Sets status to REVIEWED."""
    cs = load_change_set(thread_id, workspace_dir)
    if cs is None:
        cs = collect_changes(thread_id, workspace_dir)

    for f in cs.files:
        if not f.risk or f.risk == "medium":
            f.risk = _classify_risk(f.path, f.change_type, f.additions, f.deletions)

    cs.status = ChangeSetStatus.REVIEWED
    cs.generated_at = datetime.now(timezone.utc).isoformat()
    save_change_set(cs)
    return cs


def approve_changes(thread_id: str, approved: bool, comment: str = "", workspace_dir: str | None = None) -> ChangeSet:
    """Approve or reject the collected change set."""
    cs = load_change_set(thread_id, workspace_dir)
    if cs is None:
        raise ValueError(f"No change set found for run {thread_id}")

    cs.status = ChangeSetStatus.APPROVED if approved else ChangeSetStatus.REJECTED
    cs.generated_at = datetime.now(timezone.utc).isoformat()
    save_change_set(cs)
    return cs


def save_change_set(cs: ChangeSet) -> Path:
    """Persist change set as changes.json via atomic write."""
    rd = _run_dir(cs.thread_id, cs.workspace_dir)
    path = rd / "changes.json"
    _write_json_atomic(path, cs.model_dump())
    return path


def load_change_set(thread_id: str, workspace_dir: str | None = None) -> ChangeSet | None:
    """Load a previously saved change set, or None if missing/corrupt."""
    rd = _run_dir(thread_id, workspace_dir)
    path = rd / "changes.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ChangeSet(**data)
    except (json.JSONDecodeError, OSError, TypeError):
        return None
