"""Run history summaries for the AgentHub workspace."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _runs_root(workspace_dir: str | None = None) -> Path:
    return _workspace(workspace_dir) / ".nanocursor" / "runs"


def _index_path(workspace_dir: str | None = None) -> Path:
    return _runs_root(workspace_dir) / "index.json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _safe_run_dir_name(thread_id: str) -> str:
    return thread_id.replace("/", "_").replace("\\", "_")


def _session_to_index_entry(session: dict[str, Any], workspace_dir: str | None = None) -> dict[str, Any]:
    return {
        "thread_id": session.get("thread_id") or "",
        "conversation_id": session.get("conversation_id"),
        "status": session.get("status") or "unknown",
        "mode": session.get("mode") or "agenthub_delivery",
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "summary": session.get("summary") or session.get("prompt") or "",
        "workspace_dir": session.get("workspace_dir") or str(_workspace(workspace_dir)),
    }


def _build_index(runs: list[dict[str, Any]]) -> dict[str, Any]:
    cleaned = [run for run in runs if run.get("thread_id")]
    cleaned.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or 0, reverse=True)
    return {
        "schema_version": 1,
        "updated_at": time.time(),
        "runs": cleaned,
    }


def _load_index(workspace_dir: str | None = None) -> dict[str, Any] | None:
    data = _read_json(_index_path(workspace_dir))
    if not isinstance(data, dict):
        return None
    runs = data.get("runs")
    if data.get("schema_version") != 1 or not isinstance(runs, list):
        return None
    return data


def rebuild_run_index(workspace_dir: str | None = None) -> dict[str, Any]:
    """Rebuild the durable run index from existing session.json files."""
    root = _runs_root(workspace_dir)
    if not root.exists():
        index = _build_index([])
        _write_json_atomic(_index_path(workspace_dir), index)
        return index

    entries: list[dict[str, Any]] = []
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        session = _read_json(run_dir / "session.json")
        if isinstance(session, dict):
            entries.append(_session_to_index_entry(session, workspace_dir))

    index = _build_index(entries)
    _write_json_atomic(_index_path(workspace_dir), index)
    return index


def upsert_run_index(session: dict[str, Any], workspace_dir: str | None = None) -> None:
    """Insert or update one run in <workspace>/.nanocursor/runs/index.json."""
    thread_id = session.get("thread_id")
    if not thread_id:
        return

    index = _load_index(workspace_dir) or rebuild_run_index(workspace_dir)
    existing = [
        item for item in index.get("runs", [])
        if isinstance(item, dict) and item.get("thread_id") != thread_id
    ]
    existing.append(_session_to_index_entry(session, workspace_dir))
    _write_json_atomic(_index_path(workspace_dir), _build_index(existing))


def _count_events(path: Path) -> tuple[int, str | None]:
    if not path.exists():
        return 0, None

    count = 0
    last_event_type = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, None

    for line in lines:
        if not line.strip():
            continue
        count += 1
        try:
            event = json.loads(line)
            last_event_type = event.get("type") or last_event_type
        except json.JSONDecodeError:
            continue
    return count, last_event_type


def _changed_files_count(path: Path) -> int:
    data = _read_json(path)
    return len(data) if isinstance(data, list) else 0


def list_run_history(
    workspace_dir: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return run sessions with lightweight artifact metadata."""
    root = _runs_root(workspace_dir)
    if not root.exists():
        return []

    index = _load_index(workspace_dir) or rebuild_run_index(workspace_dir)
    indexed_ids = [
        item.get("thread_id")
        for item in index.get("runs", [])
        if isinstance(item, dict) and item.get("thread_id")
    ]

    # Pick up legacy run directories that existed before the index was created.
    known_ids = set(indexed_ids)
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        session = _read_json(run_dir / "session.json")
        thread_id = session.get("thread_id") if isinstance(session, dict) else None
        thread_id = thread_id or run_dir.name
        if thread_id not in known_ids:
            indexed_ids.append(thread_id)
            known_ids.add(thread_id)

    runs: list[dict[str, Any]] = []
    for thread_id in indexed_ids:
        run_dir = root / _safe_run_dir_name(thread_id)

        session = _read_json(run_dir / "session.json")
        if not isinstance(session, dict):
            continue

        if status and session.get("status") != status:
            continue
        if mode and session.get("mode") != mode:
            continue

        event_count, last_event_type = _count_events(run_dir / "events.jsonl")
        changed_files_count = _changed_files_count(run_dir / "changed_files.json")

        runs.append(
            {
                "thread_id": session.get("thread_id") or run_dir.name,
                "workspace_dir": session.get("workspace_dir") or str(_workspace(workspace_dir)),
                "status": session.get("status") or "unknown",
                "prompt": session.get("prompt") or "",
                "mode": session.get("mode") or "agenthub_delivery",
                "created_at": session.get("created_at"),
                "updated_at": session.get("updated_at"),
                "event_count": event_count,
                "changed_files_count": changed_files_count,
                "has_diff": (run_dir / "diff.patch").exists(),
                "has_report": (run_dir / "report.md").exists(),
                "last_event_type": last_event_type,
            }
        )

    runs.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or 0, reverse=True)
    return runs[: max(limit, 0)]
