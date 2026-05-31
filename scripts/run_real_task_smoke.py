#!/usr/bin/env python3
"""Run nanoCursor real-task smoke checks against a running API server.

This script intentionally uses the public HTTP API. It is meant for local
operator validation after larger changes, not for default CI. The validation
logic is importable and covered by unit tests with synthetic outcomes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "real_tasks"


@dataclass(frozen=True)
class SmokeTask:
    task_id: str
    label: str
    fixture: str
    prompt: str
    expected_strategies: tuple[str, ...]
    min_changed_files: int = 0
    max_changed_files: int | None = None
    required_stage_ids: tuple[str, ...] = ()
    require_final_message: bool = True
    require_diff: bool = False
    require_runtime_team: bool = False
    require_agent_activity: bool = False


TASKS: tuple[SmokeTask, ...] = (
    SmokeTask(
        task_id="readme_analysis",
        label="Read-only README analysis",
        fixture="readme_only",
        prompt="只分析这个项目，不修改任何文件。请指出 README 里最需要改进的一点。",
        expected_strategies=("analysis_only",),
        max_changed_files=0,
        required_stage_ids=("intake", "plan"),
    ),
    SmokeTask(
        task_id="tiny_python_slugify",
        label="Tiny Python slugify change",
        fixture="tiny_python_pkg",
        prompt="新增一个 slugify 函数并补一个最小测试。",
        expected_strategies=("feature_delivery", "small_patch"),
        min_changed_files=1,
        require_diff=True,
    ),
    SmokeTask(
        task_id="tiny_frontend_mixed",
        label="Tiny frontend mixed improvement",
        fixture="tiny_frontend",
        prompt="检查前端入口和 README，做一个很小的可运行性改进，并更新说明。",
        expected_strategies=("feature_delivery", "small_patch", "docs_only"),
        min_changed_files=1,
        require_diff=True,
        require_runtime_team=True,
        require_agent_activity=True,
    ),
)


def _task_by_id(task_id: str) -> SmokeTask:
    for task in TASKS:
        if task.task_id == task_id:
            return task
    raise KeyError(f"Unknown task id: {task_id}")


def _post_json(base_url: str, path: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(base_url: str, path: str, timeout: int) -> dict[str, Any]:
    with urllib.request.urlopen(f"{base_url.rstrip('/')}{path}", timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def prepare_workspace(task: SmokeTask, root: Path) -> Path:
    fixture = FIXTURES_ROOT / task.fixture
    if not fixture.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture}")

    workspace = root / task.task_id
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(fixture, workspace)
    return workspace


def wait_for_outcome(
    base_url: str,
    thread_id: str,
    *,
    timeout_seconds: int,
    poll_interval: float = 1.5,
    request_timeout: int = 30,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        last = _get_json(base_url, f"/api/runs/{thread_id}/outcome", request_timeout)
        if last.get("status") in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(poll_interval)
    raise TimeoutError(f"Run {thread_id} did not finish within {timeout_seconds}s. Last outcome: {last}")


def load_events(base_url: str, thread_id: str, request_timeout: int = 30) -> list[dict[str, Any]]:
    try:
        result = _get_json(base_url, f"/api/runs/{thread_id}/events/history", request_timeout)
    except (urllib.error.URLError, TimeoutError):
        return []
    events = result.get("events")
    return events if isinstance(events, list) else []


def validate_outcome(
    task: SmokeTask,
    outcome: dict[str, Any],
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    events = events or []

    strategy = str(outcome.get("strategy") or "")
    if task.expected_strategies and strategy not in task.expected_strategies:
        errors.append(f"strategy expected one of {task.expected_strategies}, got {strategy!r}")

    status = str(outcome.get("status") or "")
    if status != "completed":
        errors.append(f"status expected 'completed', got {status!r}")

    summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
    if task.require_final_message and not str(summary.get("final_message") or "").strip():
        errors.append("final_message is missing")

    stages = outcome.get("stages") if isinstance(outcome.get("stages"), list) else []
    stage_ids = [str(stage.get("id") or "") for stage in stages if isinstance(stage, dict)]
    for stage_id in task.required_stage_ids:
        if stage_id not in stage_ids:
            errors.append(f"required stage {stage_id!r} is missing")

    changes = outcome.get("changes") if isinstance(outcome.get("changes"), dict) else {}
    files = changes.get("files") if isinstance(changes.get("files"), list) else []
    file_count = len(files)
    if file_count < task.min_changed_files:
        errors.append(f"changed files expected >= {task.min_changed_files}, got {file_count}")
    if task.max_changed_files is not None and file_count > task.max_changed_files:
        errors.append(f"changed files expected <= {task.max_changed_files}, got {file_count}")
    if task.require_diff and not str(changes.get("diff") or "").strip():
        errors.append("diff is required but missing")

    team = outcome.get("team") if isinstance(outcome.get("team"), dict) else {}
    members = team.get("members") if isinstance(team.get("members"), list) else []
    runtime_source = str(team.get("runtime_source") or "")
    if task.require_runtime_team and (len(members) <= 1 or runtime_source not in {"runtime_recommended", "conversation"}):
        errors.append("runtime team did not expand beyond Lead")

    if task.require_agent_activity:
        event_types = {str(event.get("type") or "") for event in events if isinstance(event, dict)}
        if not ({"ephemeral_agent_spawned", "parallel_agents_started", "parallel_briefing_injected"} & event_types):
            errors.append("no temporary/parallel agent activity was observed")

    return {
        "task_id": task.task_id,
        "label": task.label,
        "ok": not errors,
        "errors": errors,
        "summary": {
            "thread_id": outcome.get("thread_id"),
            "status": status,
            "strategy": strategy,
            "stage_ids": stage_ids,
            "changed_files_count": file_count,
            "runtime_team_source": runtime_source,
            "event_count": len(events),
            "final_message_preview": str(summary.get("final_message") or "")[:240],
        },
    }


def run_task(task: SmokeTask, args: argparse.Namespace, workspace_root: Path) -> dict[str, Any]:
    workspace = prepare_workspace(task, workspace_root)
    created = _post_json(
        args.base_url,
        "/api/conversations",
        {"prompt": task.prompt, "workspace_dir": str(workspace)},
        args.request_timeout,
    )
    conversation_id = created["conversation"]["conversation_id"]
    started = _post_json(
        args.base_url,
        f"/api/conversations/{conversation_id}/runs",
        {"prompt": task.prompt, "workspace_dir": str(workspace)},
        args.request_timeout,
    )
    thread_id = started["run"]["thread_id"]
    outcome = wait_for_outcome(
        args.base_url,
        thread_id,
        timeout_seconds=args.timeout,
        poll_interval=args.poll_interval,
        request_timeout=args.request_timeout,
    )
    events = load_events(args.base_url, thread_id, args.request_timeout)
    result = validate_outcome(task, outcome, events)
    result["workspace_dir"] = str(workspace)
    result["conversation_id"] = conversation_id
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8100", help="nanoCursor API base URL")
    parser.add_argument("--task", action="append", choices=[task.task_id for task in TASKS], help="Task id to run")
    parser.add_argument("--timeout", type=int, default=180, help="Seconds to wait for each run")
    parser.add_argument("--poll-interval", type=float, default=1.5, help="Polling interval in seconds")
    parser.add_argument("--request-timeout", type=int, default=30, help="HTTP request timeout in seconds")
    parser.add_argument("--workspace-root", default="", help="Workspace root to reuse instead of a temp directory")
    parser.add_argument("--keep-workspaces", action="store_true", help="Do not delete temporary workspaces")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    selected = [_task_by_id(task_id) for task_id in args.task] if args.task else list(TASKS)

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if args.workspace_root:
        workspace_root = Path(args.workspace_root).expanduser().resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
    elif args.keep_workspaces:
        workspace_root = Path(tempfile.mkdtemp(prefix="nanocursor-real-task-"))
    else:
        temp_dir = tempfile.TemporaryDirectory(prefix="nanocursor-real-task-")
        workspace_root = Path(temp_dir.name)

    try:
        results = [run_task(task, args, workspace_root) for task in selected]
    finally:
        if temp_dir is not None and not args.keep_workspaces:
            temp_dir.cleanup()

    output = {
        "ok": all(result["ok"] for result in results),
        "base_url": args.base_url,
        "workspace_root": str(workspace_root),
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
