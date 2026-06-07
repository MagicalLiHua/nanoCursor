"""Deterministic runtime-quality scenarios for the aggregate Agent Eval gate."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.api.services.event_store import get_event_store
from src.api.services.run_eval_metrics_service import build_run_eval_metrics


RUNTIME_QUALITY_METRIC_CASE_IDS = [
    "direct_answer_metrics",
    "read_only_metrics",
    "small_edit_metrics",
    "approval_metrics",
    "failure_recovery_metrics",
]


def run_runtime_quality_metrics_section(workspace: Path) -> dict[str, Any]:
    cases = [
        _direct_answer(workspace),
        _read_only(workspace),
        _small_edit(workspace),
        _approval(workspace),
        _failure_recovery(workspace),
    ]
    passed = sum(case["status"] == "passed" for case in cases)
    return {
        "id": "runtime_quality_metrics",
        "label": "Runtime Quality Metrics",
        "status": "passed" if passed == len(cases) else "failed",
        "total": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "pass_rate": round(passed / max(len(cases), 1), 3),
        "cases": cases,
        "summary": "直接回答、只读、小修改、审批和失败恢复场景的证据指标评测。",
    }


def _direct_answer(workspace: Path) -> dict[str, Any]:
    probe = _probe_workspace(workspace, "metrics-direct")
    thread_id = _thread_id("metrics-direct")
    store = get_event_store()
    store.create_session(thread_id, "哈喽", str(probe), status="completed")
    store.update_session(thread_id, str(probe), execution_plan={"strategy": "lead_direct_reply"})
    store.append_event(
        thread_id,
        "loop_turn_finished",
        payload={"turn_id": "direct-turn", "step": 1, "action_type": "answer"},
        workspace_dir=str(probe),
    )
    store.append_event(thread_id, "assistant_message", content="你好", workspace_dir=str(probe))
    metrics = build_run_eval_metrics(thread_id, str(probe))
    return _result("direct_answer_metrics", [
        _check("turn_count_is_minimal", metrics["metrics"]["turn_count"]["value"] == 1, metrics["metrics"]["turn_count"], 1),
        _check("no_tool_penalty", metrics["metrics"]["tool_execution_rate"]["status"] == "not_applicable", metrics["metrics"]["tool_execution_rate"], "not_applicable"),
        _check("overall_passed", metrics["overall_status"] == "passed", metrics["overall_status"], "passed"),
    ])


def _read_only(workspace: Path) -> dict[str, Any]:
    probe = _probe_workspace(workspace, "metrics-read-only")
    thread_id = _thread_id("metrics-read")
    store = get_event_store()
    store.create_session(thread_id, "查看 README.md", str(probe), status="completed")
    store.update_session(thread_id, str(probe), execution_plan={"strategy": "analysis_only"})
    _write_pack(probe, thread_id, {
        "id": "read-only-pack",
        "selected_files": [{"path": "README.md", "reasons": ["prompt matched README"]}],
        "selected_memories": [],
    })
    store.append_event(
        thread_id,
        "tool_call_finished",
        payload={"tool": "read_file", "input": {"path": "README.md"}, "output": "# probe", "ok": True},
        workspace_dir=str(probe),
    )
    metrics = build_run_eval_metrics(thread_id, str(probe))
    return _result("read_only_metrics", [
        _check("context_relevance", metrics["metrics"]["context_relevance"]["value"] == 1.0, metrics["metrics"]["context_relevance"], 1.0),
        _check("tool_execution", metrics["metrics"]["tool_execution_rate"]["value"] == 1.0, metrics["metrics"]["tool_execution_rate"], 1.0),
        _check("no_approval_needed", metrics["metrics"]["approval_resolution_rate"]["status"] == "not_applicable", metrics["metrics"]["approval_resolution_rate"], "not_applicable"),
    ])


def _small_edit(workspace: Path) -> dict[str, Any]:
    probe = _probe_workspace(workspace, "metrics-small-edit")
    thread_id = _thread_id("metrics-edit")
    store = get_event_store()
    store.create_session(thread_id, "修复 app.py 并运行测试", str(probe), status="completed")
    store.update_session(thread_id, str(probe), execution_plan={"strategy": "small_edit"})
    _write_pack(probe, thread_id, {
        "id": "small-edit-pack",
        "selected_files": [
            {"path": "app.py", "reasons": ["prompt matched app"]},
            {"path": "test_app.py", "reasons": ["test/source relation"]},
        ],
        "selected_memories": [],
    })
    for step in range(1, 4):
        store.append_event(thread_id, "loop_turn_finished", payload={"turn_id": f"edit-turn-{step}", "step": step}, workspace_dir=str(probe))
    store.append_event(
        thread_id,
        "tool_call_finished",
        payload={"tool": "edit_file", "input": {"path": "app.py"}, "output": "updated", "ok": True},
        workspace_dir=str(probe),
    )
    store.append_event(thread_id, "file_changed", payload={"path": "app.py"}, workspace_dir=str(probe))
    store.append_event(thread_id, "test_finished", content="test_app.py passed", payload={"status": "passed"}, workspace_dir=str(probe))
    metrics = build_run_eval_metrics(thread_id, str(probe))
    return _result("small_edit_metrics", [
        _check("turn_count_recorded", metrics["metrics"]["turn_count"]["value"] == 3, metrics["metrics"]["turn_count"], 3),
        _check("context_relevance", metrics["metrics"]["context_relevance"]["value"] == 1.0, metrics["metrics"]["context_relevance"], 1.0),
        _check("tool_execution", metrics["metrics"]["tool_execution_rate"]["value"] == 1.0, metrics["metrics"]["tool_execution_rate"], 1.0),
    ])


def _approval(workspace: Path) -> dict[str, Any]:
    probe = _probe_workspace(workspace, "metrics-approval")
    thread_id = _thread_id("metrics-approval")
    store = get_event_store()
    store.create_session(thread_id, "执行高风险操作", str(probe), status="completed")
    store.append_event(thread_id, "approval_requested", payload={"approval_id": "approval-1"}, workspace_dir=str(probe))
    store.append_event(thread_id, "approval_resolved", payload={"approval_id": "approval-1", "approved": True}, workspace_dir=str(probe))
    metrics = build_run_eval_metrics(thread_id, str(probe))
    return _result("approval_metrics", [
        _check("approval_resolution", metrics["metrics"]["approval_resolution_rate"]["value"] == 1.0, metrics["metrics"]["approval_resolution_rate"], 1.0),
        _check("approval_passed", metrics["metrics"]["approval_resolution_rate"]["status"] == "passed", metrics["metrics"]["approval_resolution_rate"], "passed"),
    ])


def _failure_recovery(workspace: Path) -> dict[str, Any]:
    probe = _probe_workspace(workspace, "metrics-recovery")
    original_id = _thread_id("original-failed")
    retry_id = _thread_id("retry-completed")
    store = get_event_store()
    store.create_session(original_id, "修复测试", str(probe), status="failed")
    store.append_event(original_id, "error", content="test_app.py failed", workspace_dir=str(probe))
    store.create_session(retry_id, "继续修复测试", str(probe), status="completed", mode="retry")
    store.update_session(
        retry_id,
        str(probe),
        original_thread_id=original_id,
        original_status="failed",
        retry_context={"recent_errors": [{"content": "test_app.py failed"}]},
    )
    _write_pack(probe, retry_id, {
        "id": "recovery-pack",
        "selected_files": [{"path": "test_app.py", "reasons": ["recent failure related"]}],
        "selected_memories": [],
        "recovery_context": {"original_thread_id": original_id},
    })
    metrics = build_run_eval_metrics(retry_id, str(probe))
    recovery = metrics["metrics"]["recovery_success_rate"]
    return _result("failure_recovery_metrics", [
        _check("recovery_success", recovery["value"] == 1.0, recovery, 1.0),
        _check("failure_context_hit", recovery["evidence"]["retry_context_pack_ids"] == ["recovery-pack"], recovery, ["recovery-pack"]),
    ])


def _probe_workspace(workspace: Path, name: str) -> Path:
    probe = workspace / ".nanocursor" / "eval_probe_workspaces" / f"{name}-{int(time.time() * 1000)}"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (probe / "test_app.py").write_text("from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    (probe / "README.md").write_text("# probe workspace\n", encoding="utf-8")
    return probe


def _write_pack(workspace: Path, thread_id: str, data: dict[str, Any]) -> None:
    path = workspace / ".nanocursor" / "runs" / thread_id / "context" / "packs" / f"{data['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _thread_id(label: str) -> str:
    return f"agent-eval-{label}-{time.time_ns()}"


def _check(check_id: str, ok: bool, actual: Any, expected: Any) -> dict[str, Any]:
    return {"id": check_id, "status": "passed" if ok else "failed", "actual": actual, "expected": expected}


def _result(case_id: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": case_id, "status": "passed" if all(check["status"] == "passed" for check in checks) else "failed", "checks": checks}
