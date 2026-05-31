"""Diff helpers for nanoCursor runs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_dir(thread_id: str, workspace_dir: str | None = None) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return _workspace(workspace_dir) / ".nanocursor" / "runs" / safe_id


def _run_git(workspace: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _parse_status(output: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        raw_status = line[:2]
        status = raw_status.strip() or "M"
        path = line[3:].strip()
        old_path = ""
        if " -> " in path:
            old_path, path = path.split(" -> ", 1)
        status_chars = set(raw_status)
        if status == "??" or "A" in status_chars or "C" in status_chars:
            change_type = "created"
        elif "R" in status_chars:
            change_type = "renamed"
        elif "D" in status_chars:
            change_type = "deleted"
        else:
            change_type = "modified"
        item = {"path": path, "status": status, "change_type": change_type}
        if old_path:
            item["old_path"] = old_path
        files.append(item)
    return files


def _is_internal_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    return normalized == ".nanocursor" or normalized.startswith((".nanocursor/", ".backups/", ".tasks/", ".snapshots/"))


def _diff_contains_path(diff: str, path: str) -> bool:
    return f" b/{path}" in diff or f"+++ b/{path}" in diff or f"--- a/{path}" in diff


def _safe_workspace_file(workspace: Path, path: str) -> Path | None:
    if not path or path.startswith(("/", "\\")):
        return None
    candidate = (workspace / path).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _is_binary_file(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:8192]
    except OSError:
        return False
    return b"\0" in chunk


def _expand_untracked_directories(workspace: Path, changed_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for item in changed_files:
        path = str(item.get("path") or "").strip()
        if item.get("status") != "??" or not path.endswith("/"):
            expanded.append(item)
            continue

        directory = (workspace / path).resolve()
        try:
            directory.relative_to(workspace)
        except ValueError:
            expanded.append(item)
            continue
        if not directory.is_dir():
            expanded.append(item)
            continue

        found_files = []
        for child in sorted(directory.rglob("*")):
            if not child.is_file():
                continue
            relative = child.relative_to(workspace).as_posix()
            if "/.git/" in f"/{relative}/":
                continue
            item = {"path": relative, "status": "??", "change_type": "created"}
            if _is_binary_file(child):
                item["binary"] = True
            found_files.append(item)
        expanded.extend(found_files or [item])
    return expanded


def _new_file_patch(workspace: Path, path: str) -> str:
    file_path = _safe_workspace_file(workspace, path)
    if file_path is None:
        return ""

    if _is_binary_file(file_path):
        return "\n".join(
            [
                f"diff --git a/{path} b/{path}",
                "new file mode 100644",
                "index 0000000..0000000",
                f"Binary files /dev/null and b/{path} differ",
            ]
        ) + "\n"

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    lines = content.splitlines()
    if content.endswith("\n"):
        content_lines = [f"+{line}" for line in lines]
    else:
        content_lines = [f"+{line}" for line in lines]
        if lines:
            content_lines.append(r"\ No newline at end of file")

    line_count = len(lines)
    patch_lines = [
        f"diff --git a/{path} b/{path}",
        "new file mode 100644",
        "index 0000000..0000000",
        "--- /dev/null",
        f"+++ b/{path}",
        f"@@ -0,0 +{1 if line_count else 0},{line_count} @@",
    ]
    if content_lines:
        patch_lines.extend(content_lines)
    return "\n".join(patch_lines) + "\n"


def _append_untracked_file_patches(workspace: Path, diff: str, changed_files: list[dict[str, Any]]) -> str:
    patches: list[str] = []
    for item in changed_files:
        path = str(item.get("path") or "").strip()
        if item.get("status") != "??" or not path or _diff_contains_path(diff, path):
            continue
        patch = _new_file_patch(workspace, path)
        if patch:
            patches.append(patch)
    if not patches:
        return diff
    return "\n".join(part for part in [diff.rstrip(), *[patch.rstrip() for patch in patches]] if part) + "\n"


def _changed_files_from_events(thread_id: str, workspace_dir: str | None = None) -> list[dict[str, Any]]:
    """Derive changed files from run events when git cannot see the workspace."""
    from src.api.services.event_store import get_event_store

    files_by_path: dict[str, dict[str, Any]] = {}
    for event in get_event_store().list_events(thread_id, str(_workspace(workspace_dir))):
        if event.type != "file_changed" or not isinstance(event.payload, dict):
            continue
        path = str(event.payload.get("path") or "").strip()
        if not path:
            continue
        output = str(event.payload.get("output") or event.content or "")
        change_type = "created" if output.startswith("Created ") else str(event.payload.get("change_type") or "modified")
        files_by_path[path] = {
            "path": path,
            "status": "event",
            "change_type": "created" if change_type == "added" else change_type,
        }
    return [files_by_path[path] for path in sorted(files_by_path)]


def get_run_diff(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Return unified diff and changed files for a run or active workspace."""
    workspace = _workspace(workspace_dir)
    run_dir = _run_dir(thread_id, workspace_dir)
    patch_path = run_dir / "diff.patch"
    changed_files_path = run_dir / "changed_files.json"

    if patch_path.exists():
        diff = patch_path.read_text(encoding="utf-8", errors="replace")
        changed_files = []
        if changed_files_path.exists():
            try:
                changed_files = json.loads(changed_files_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                changed_files = []
        return {
            "thread_id": thread_id,
            "workspace_dir": str(workspace),
            "diff": diff,
            "changed_files": changed_files,
            "source": "run_artifact",
        }

    status_result = _run_git(workspace, ["status", "--short", "--", "."])
    diff_result = _run_git(workspace, ["diff", "--no-ext-diff", "HEAD", "--", "."])
    if diff_result.returncode != 0:
        diff_result = _run_git(workspace, ["diff", "--no-ext-diff", "--", "."])

    diff = diff_result.stdout if diff_result.returncode == 0 else ""
    changed_files = _parse_status(status_result.stdout) if status_result.returncode == 0 else []
    changed_files = _expand_untracked_directories(workspace, changed_files)
    for item in changed_files:
        if item.get("status") == "??" and "binary" not in item:
            file_path = _safe_workspace_file(workspace, str(item.get("path") or ""))
            if file_path is not None and _is_binary_file(file_path):
                item["binary"] = True
    changed_files = [item for item in changed_files if not _is_internal_path(str(item.get("path") or ""))]
    if changed_files:
        diff = _append_untracked_file_patches(workspace, diff, changed_files)
    source = "git"
    if not changed_files:
        changed_files = _changed_files_from_events(thread_id, str(workspace))
        if changed_files:
            source = "events"

    return {
        "thread_id": thread_id,
        "workspace_dir": str(workspace),
        "diff": diff,
        "changed_files": changed_files,
        "source": source,
        "error": diff_result.stderr.strip() if diff_result.returncode != 0 else "",
    }
