"""Requirement traceability matrix generation for AgentHub runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.api.models import AgentEvent
from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store
from src.infra import config as config_module


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_dir(thread_id: str, workspace_dir: str | None = None) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return _workspace(workspace_dir) / ".nanocursor" / "runs" / safe_id


def _status_for(tasks: list[str], files: list[str], tests: list[str]) -> str:
    if tasks and files and tests:
        return "covered"
    if tasks or files or tests:
        return "partial"
    return "missing"


def _event_payload(event: AgentEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _task_titles(events: list[AgentEvent]) -> list[str]:
    titles: list[str] = []
    for event in events:
        if event.type != "task_created":
            continue
        payload = _event_payload(event)
        task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
        task_id = payload.get("task_id") or task.get("id")
        title = task.get("title") or event.title
        if task_id and title:
            titles.append(f"{task_id}: {title}")
        elif title:
            titles.append(str(title))
    return titles


def _test_checks(events: list[AgentEvent]) -> list[str]:
    checks: list[str] = []
    for event in events:
        if event.type != "test_finished":
            continue
        payload = _event_payload(event)
        raw_checks = payload.get("checks")
        if isinstance(raw_checks, list):
            checks.extend(str(item) for item in raw_checks if item)
        elif event.content:
            checks.append(event.content)
    return checks


def _normalize_requirement(item: dict[str, Any]) -> dict[str, Any]:
    tasks = [str(value) for value in item.get("tasks", []) if value]
    files = [str(value) for value in item.get("files", []) if value]
    tests = [str(value) for value in item.get("tests", []) if value]
    status = item.get("status") or _status_for(tasks, files, tests)
    return {
        "id": str(item.get("id") or "REQ"),
        "title": str(item.get("title") or item.get("description") or "Untitled requirement"),
        "description": str(item.get("description") or ""),
        "status": str(status),
        "tasks": tasks,
        "files": files,
        "tests": tests,
        "evidence": item.get("evidence") if isinstance(item.get("evidence"), dict) else {},
    }


def _load_requirements(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    items = raw.get("requirements") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return None
    return [_normalize_requirement(item) for item in items if isinstance(item, dict)]


def _fallback_requirements(thread_id: str, workspace: Path) -> list[dict[str, Any]]:
    store = get_event_store()
    session = store.get_session(thread_id, str(workspace))
    events = store.list_events(thread_id, str(workspace))
    diff = get_run_diff(thread_id, str(workspace))

    prompt = session.get("prompt", "") if session else ""
    tasks = _task_titles(events)
    files = [str(item.get("path")) for item in diff.get("changed_files", []) if item.get("path")]
    tests = _test_checks(events)
    title = prompt[:80] if prompt else "Recorded delivery request"

    return [
        _normalize_requirement(
            {
                "id": "REQ-001",
                "title": title,
                "description": prompt,
                "tasks": tasks,
                "files": files,
                "tests": tests,
                "evidence": {
                    "source": "generated_from_run_events",
                    "event_count": len(events),
                    "changed_files_count": len(files),
                },
            }
        )
    ]


def build_requirement_traceability(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Build the requirement-to-task/file/test coverage matrix for a run."""
    workspace = _workspace(workspace_dir)
    requirements_path = _run_dir(thread_id, str(workspace)) / "requirements.json"
    requirements = _load_requirements(requirements_path)
    source = "run_artifact" if requirements is not None else "generated"

    if requirements is None:
        requirements = _fallback_requirements(thread_id, workspace)

    covered_count = sum(1 for item in requirements if item["status"] == "covered")
    partial_count = sum(1 for item in requirements if item["status"] == "partial")
    missing_count = sum(1 for item in requirements if item["status"] == "missing")
    total_count = len(requirements)
    coverage_rate = round(covered_count / total_count, 4) if total_count else 0.0

    return {
        "thread_id": thread_id,
        "workspace_dir": str(workspace),
        "source": source,
        "total_count": total_count,
        "covered_count": covered_count,
        "partial_count": partial_count,
        "missing_count": missing_count,
        "coverage_rate": coverage_rate,
        "requirements": requirements,
    }
