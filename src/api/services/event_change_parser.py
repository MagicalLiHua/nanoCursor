"""Recover run change evidence from durable file events.

The normal path is git diff. For ignored, temporary, or non-git workspaces we
still persist file_changed events with tool output previews. Those previews often
contain fenced unified diffs, which are enough to show useful change evidence.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.api.services.change_path_filter import should_hide_change_path


_FENCED_DIFF_RE = re.compile(r"```diff\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_fenced_diff(text: str) -> str:
    """Extract concatenated fenced diff blocks from tool output."""
    blocks = [match.group(1).strip("\n") for match in _FENCED_DIFF_RE.finditer(text or "")]
    return "\n".join(block for block in blocks if block).strip()


def diff_line_stats(diff: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in (diff or "").splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def diff_stats_by_path(diff: str) -> dict[str, dict[str, int]]:
    """Return additions/deletions/hunks per file from a unified diff."""
    stats: dict[str, dict[str, int]] = {}
    current = ""
    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            current = ""
            continue
        if line.startswith("+++ b/"):
            current = line[6:].strip()
            stats.setdefault(current, {"additions": 0, "deletions": 0, "hunks": 0})
            continue
        if line.startswith("--- a/") and not current:
            current = line[6:].strip()
            stats.setdefault(current, {"additions": 0, "deletions": 0, "hunks": 0})
            continue
        if not current:
            continue
        if line.startswith("@@"):
            stats[current]["hunks"] += 1
        elif line.startswith("+") and not line.startswith("+++"):
            stats[current]["additions"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            stats[current]["deletions"] += 1
    return stats


def _is_internal_path(path: str) -> bool:
    return should_hide_change_path(path)


def _safe_workspace_file(workspace: Path, path: str) -> Path | None:
    if not path or path.startswith(("/", "\\")):
        return None
    candidate = (workspace / path).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _is_binary_file(path: Path) -> bool:
    try:
        return b"\0" in path.read_bytes()[:8192]
    except OSError:
        return False


def _new_file_patch(workspace: Path, path: str) -> tuple[str, int]:
    file_path = _safe_workspace_file(workspace, path)
    if file_path is None:
        return "", 0
    if _is_binary_file(file_path):
        return (
            "\n".join(
                [
                    f"diff --git a/{path} b/{path}",
                    "new file mode 100644",
                    "index 0000000..0000000",
                    "--- /dev/null",
                    f"+++ b/{path}",
                    f"Binary files /dev/null and b/{path} differ",
                ]
            ),
            0,
        )
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", 0
    lines = content.splitlines()
    patch_lines = [
        f"diff --git a/{path} b/{path}",
        "new file mode 100644",
        "index 0000000..0000000",
        "--- /dev/null",
        f"+++ b/{path}",
        f"@@ -0,0 +{1 if lines else 0},{len(lines)} @@",
        *[f"+{line}" for line in lines],
    ]
    if content and not content.endswith("\n"):
        patch_lines.append(r"\ No newline at end of file")
    return "\n".join(patch_lines), len(lines)


def collect_event_changes(thread_id: str, workspace_dir: str | Path) -> tuple[str, list[dict[str, Any]]]:
    """Collect changed file metadata and synthetic diff text from file events."""
    from src.api.services.event_store import get_event_store

    workspace_path = Path(workspace_dir).resolve()
    workspace = str(workspace_path)
    files_by_path: dict[str, dict[str, Any]] = {}
    diff_blocks: list[str] = []

    for event in get_event_store().list_events(thread_id, workspace):
        if event.type != "file_changed" or not isinstance(event.payload, dict):
            continue

        path = str(event.payload.get("path") or "").strip()
        if not path or _is_internal_path(path):
            continue

        output = str(event.payload.get("output") or event.content or "")
        change_type = str(event.payload.get("change_type") or "modified")
        if output.startswith("Created "):
            change_type = "created"
        elif change_type == "added":
            change_type = "created"

        diff = extract_fenced_diff(output)
        path_stats = diff_stats_by_path(diff).get(path, {})
        additions = int(path_stats.get("additions") or 0)
        deletions = int(path_stats.get("deletions") or 0)
        hunks = int(path_stats.get("hunks") or 0)
        if diff and not (additions or deletions):
            additions, deletions = diff_line_stats(diff)
            hunks = max(hunks, diff.count("\n@@ "))
        if not diff and change_type == "created":
            diff, line_count = _new_file_patch(workspace_path, path)
            additions = additions or line_count
            hunks = hunks or (1 if diff else 0)

        item: dict[str, Any] = {
            "path": path,
            "status": "event",
            "change_type": change_type,
        }
        if additions or deletions:
            item["additions"] = additions
            item["deletions"] = deletions
        if hunks:
            item["hunks"] = hunks
        if output:
            item["summary"] = output[:240]

        files_by_path[path] = item
        if diff:
            diff_blocks.append(diff)

    return "\n".join(diff_blocks).strip(), [files_by_path[path] for path in sorted(files_by_path)]
