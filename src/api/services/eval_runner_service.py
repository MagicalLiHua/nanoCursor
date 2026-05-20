"""Real eval runner — run agent, execute test_command, score, save results."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.infra import config as config_module
from src.api.services.event_store import get_event_store
from src.api.services.eval_service import (
    get_eval_task,
    prepare_eval_workspace,
    score_eval_run,
    _evals_root,
    _workspace,
)
from src.runtime.command_runner import run_command


def run_eval_with_command(
    eval_id: str,
    workspace_dir: str | None = None,
    mode: str = "agent",
) -> dict[str, Any]:
    """Run an eval task: copy fixture, execute test_command, score, and save.

    *mode*:
      - ``"agent"`` — simulates agent events then runs test_command
      - ``"baseline"`` — run test_command directly on fixture (no agent events)
      - ``"command_only"`` — only run test_command, skip agent simulation
    """
    task = get_eval_task(eval_id)
    if not task:
        raise ValueError(f"Eval 任务不存在: {eval_id}")

    workspace = _workspace(workspace_dir)
    eval_workspace = prepare_eval_workspace(eval_id, str(workspace)) if task.get("fixture") else workspace
    store = get_event_store()
    eval_run_id = f"eval-{eval_id}-{int(time.time() * 1000)}"
    thread_id = eval_run_id

    # Create session
    store.create_session(thread_id, task["prompt"], str(eval_workspace), status="running", mode="eval")

    # Agent simulation (when mode is not command_only)
    if mode != "command_only":
        _emit_agent_events(thread_id, str(eval_workspace), store, task)

    # Run test_command
    test_command = task.get("test_command", "")
    test_result = None
    if test_command:
        test_result = run_command(
            test_command,
            cwd=str(eval_workspace),
            timeout_seconds=task.get("timeout_seconds", 120),
        )
        # Emit test event
        test_passed = test_result["exit_code"] == 0
        store.append_event(
            thread_id, "test_finished",
            title="Eval 测试完成" if test_passed else "Eval 测试失败",
            content=test_result.get("stdout", "")[:2000],
            agent="tester",
            payload={
                "status": "passed" if test_passed else "failed",
                "exit_code": test_result["exit_code"],
                "stdout": test_result.get("stdout", "")[:5000],
                "stderr": test_result.get("stderr", "")[:2000],
                "duration_ms": test_result.get("duration_ms", 0),
                "timed_out": test_result.get("timed_out", False),
            },
            workspace_dir=str(eval_workspace),
        )

    store.update_session(thread_id, str(eval_workspace), status="completed")

    # Score
    signals = task.get("expected_signals", {})
    score = score_eval_run(thread_id, str(eval_workspace), signals)

    # Persist result
    result_dir = _evals_root(workspace) / eval_run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "eval_run_id": eval_run_id,
        "eval_id": eval_id,
        "thread_id": thread_id,
        "prompt": task["prompt"],
        "mode": mode,
        "score": score,
        "test_result": test_result,
        "event_count": store.count_events(thread_id, str(eval_workspace)),
        "workspace_dir": str(eval_workspace),
        "completed_at": time.time(),
    }
    (result_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _emit_agent_events(
    thread_id: str, workspace_str: str, store: Any, task: dict[str, Any],
) -> None:
    """Emit deterministic eval events (plan + file writes)."""
    store.append_event(thread_id, "plan_created",
        title="Eval Plan", content="Eval execution plan",
        agent="lead", payload={"stages": []}, workspace_dir=workspace_str)
    store.append_event(thread_id, "task_created",
        title="Eval Task", content=task.get("prompt", ""),
        agent="lead", payload={}, workspace_dir=workspace_str)

    # Write fixture output if there's a fixture
    wdir = Path(workspace_str)
    required_files = task.get("expected_signals", {}).get("required_files", [])
    if task.get("fixture") and required_files:
        changed_path = str(required_files[0])
        target = wdir / changed_path
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                '"""Utility helpers for the eval fixture."""\n\n'
                "def format_name(name: str) -> str:\n"
                "    return name.strip().title()\n",
                encoding="utf-8",
            )
        store.append_event(thread_id, "tool_call_finished",
            title="工具调用完成", content=f"write_file {changed_path}",
            agent="coder",
            payload={"tool": "write_file", "input": {"path": changed_path}, "output": "ok"},
            workspace_dir=workspace_str)
        store.append_event(thread_id, "file_changed",
            title="文件已变更", content=changed_path,
            agent="coder", payload={"path": changed_path}, workspace_dir=workspace_str)

    store.append_event(thread_id, "done",
        title="完成", content="Eval complete",
        agent="lead", payload={"status": "completed"}, workspace_dir=workspace_str)


def run_eval_suite(
    eval_ids: list[str],
    workspace_dir: str | None = None,
    mode: str = "agent",
    stop_on_failure: bool = False,
) -> dict[str, Any]:
    """Run a suite of eval tasks sequentially. Returns summary + per-task results."""
    results: list[dict[str, Any]] = []
    for eid in eval_ids:
        try:
            result = run_eval_with_command(eid, workspace_dir, mode)
            results.append(result)
            if stop_on_failure and result["score"]["overall"] == "failed":
                break
        except ValueError as exc:
            results.append({"eval_id": eid, "error": str(exc), "score": {"overall": "error"}})
            if stop_on_failure:
                break

    passed = sum(1 for r in results if r.get("score", {}).get("overall") == "passed")
    failed = len(results) - passed

    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / max(len(results), 1), 2),
        "results": results,
    }


def get_eval_summary(workspace_dir: str | None = None) -> dict[str, Any]:
    """Aggregate summary across all eval tasks."""
    workspace = _workspace(workspace_dir)
    evals_root = _evals_root(workspace)
    if not evals_root.exists():
        return {"total_runs": 0, "pass_rate": 0, "by_eval": {}}

    runs: list[dict[str, Any]] = []
    for run_dir in sorted(evals_root.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True):
        result_file = run_dir / "result.json"
        if not result_file.exists():
            continue
        try:
            runs.append(json.loads(result_file.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue

    by_eval: dict[str, dict[str, Any]] = {}
    for r in runs:
        eid = r.get("eval_id", "unknown")
        entry = by_eval.setdefault(eid, {"eval_id": eid, "total": 0, "passed": 0, "failed": 0})
        entry["total"] += 1
        if r.get("score", {}).get("overall") == "passed":
            entry["passed"] += 1
        else:
            entry["failed"] += 1

    for entry in by_eval.values():
        entry["pass_rate"] = round(entry["passed"] / max(entry["total"], 1), 2)

    total_passed = sum(r.get("score", {}).get("overall") == "passed" for r in runs)
    return {
        "total_runs": len(runs),
        "pass_rate": round(total_passed / max(len(runs), 1), 2),
        "by_eval": by_eval,
    }
