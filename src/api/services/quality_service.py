"""Rule-based quality gate checks for nanoCursor runs."""

from __future__ import annotations

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


def _event_types(events: list[AgentEvent]) -> set[str]:
    return {event.type for event in events}


def _check(
    check_id: str,
    label: str,
    ok: bool,
    severity: str,
    passed_detail: str,
    failed_detail: str,
    evidence: dict[str, Any] | None = None,
    missing_status: str | None = None,
) -> dict[str, Any]:
    status = "passed" if ok else (missing_status or ("failed" if severity == "required" else "warning"))
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "severity": severity,
        "detail": passed_detail if ok else failed_detail,
        "evidence": evidence or {},
    }


def _task_completion(events: list[AgentEvent]) -> tuple[int, int]:
    created: set[str] = set()
    completed: set[str] = set()

    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "task_created":
            task_id = payload.get("task_id")
            if task_id:
                created.add(str(task_id))
        elif event.type == "task_updated":
            task_id = payload.get("task_id")
            status = payload.get("status")
            if task_id and status == "completed":
                completed.add(str(task_id))

    return len(created), len(created & completed)


def _planned_stages(session: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not session:
        return []
    plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    return [stage for stage in stages if isinstance(stage, dict)]


def _stage_evidence_count(stages: list[dict[str, Any]]) -> int:
    count = 0
    for stage in stages:
        evidence = stage.get("tool_evidence")
        if isinstance(evidence, list):
            count += len(evidence)
    return count


def _has_verification_evidence(events: list[AgentEvent]) -> bool:
    for event in events:
        if event.type == "test_finished":
            return True
        if event.type != "tool_call_finished":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        tool = str(payload.get("tool") or "").lower()
        input_data = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        command = str(input_data.get("command") or "").lower()
        output = str(payload.get("output") or event.content or "").lower()
        ran_test_command = (
            tool in {"bash", "run_command"}
            and any(marker in command for marker in ("npm test", "pytest", "pnpm test", "yarn test"))
        )
        passed_output = any(marker in output for marker in ("tests passed", "passed", " ok", "success"))
        failed_output = any(marker in output for marker in ("tests failed", "failed", "error:", "traceback"))
        if ran_test_command and passed_output and not failed_output:
            return True
    return False


def _has_report_evidence(events: list[AgentEvent], run_dir: Path) -> bool:
    if any((run_dir / name).exists() for name in ("report.md", "delivery.md", "delivery.json")):
        return True
    for event in events:
        if event.type == "report_ready":
            return True
        if event.type == "assistant_message":
            content = str(event.content or "")
            if any(marker in content for marker in ("最终交付报告", "Delivery Report", "交付报告")):
                return True
    return False


def build_quality_gate(thread_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Build a deterministic quality gate result for a run."""
    workspace = _workspace(workspace_dir)
    run_dir = _run_dir(thread_id, str(workspace))
    store = get_event_store()
    session = store.get_session(thread_id, str(workspace))
    events = store.list_events(thread_id, str(workspace))
    types = _event_types(events)
    diff_info = get_run_diff(thread_id, str(workspace))
    changed_files = diff_info.get("changed_files", [])
    error_events = [event for event in events if event.type == "error"]
    created_tasks, completed_tasks = _task_completion(events)
    stages = _planned_stages(session)
    stage_statuses = [str(stage.get("status") or "pending") for stage in stages]
    failed_stages = [stage for stage in stages if stage.get("status") == "failed"]
    non_terminal_stages = [
        stage for stage in stages if stage.get("status") not in {"completed", "failed", "skipped"}
    ]
    required_incomplete_stages = [
        stage for stage in stages
        if stage.get("required", True) and stage.get("status") != "completed"
    ]
    stage_evidence_count = _stage_evidence_count(stages)

    done_events = [event for event in events if event.type == "done"]
    session_status = session.get("status") if session else None
    has_verification = _has_verification_evidence(events)
    has_report = _has_report_evidence(events, run_dir)
    diff_exists = (run_dir / "diff.patch").exists()

    checks = [
        _check(
            "session_recorded",
            "Run session is recorded",
            session is not None,
            "required",
            "session.json exists for this run.",
            "No run session was found.",
            {"thread_id": thread_id, "session_status": session_status},
        ),
        _check(
            "run_completed",
            "Run reached a terminal success state",
            session_status == "completed" or any(event.payload.get("status") == "completed" for event in done_events),
            "required",
            "Run completed successfully.",
            "Run has not reached completed status.",
            {"session_status": session_status, "done_events": len(done_events)},
        ),
        _check(
            "no_runtime_errors",
            "No runtime error events",
            session_status != "failed" and not error_events,
            "required",
            "No error events were recorded.",
            "One or more error events were recorded.",
            {"error_count": len(error_events)},
        ),
        _check(
            "plan_created",
            "Plan was created",
            "plan_created" in types,
            "recommended",
            "A plan_created event exists.",
            "No plan_created event was found.",
            {"event_count": len(events)},
        ),
        _check(
            "tasks_created",
            "Tasks were created",
            created_tasks > 0,
            "required",
            "Task creation events exist.",
            "No task_created events were found.",
            {"created_tasks": created_tasks},
        ),
        _check(
            "tasks_completed",
            "Created tasks were completed",
            created_tasks > 0 and completed_tasks == created_tasks,
            "recommended",
            "All created tasks reached completed status.",
            "Some created tasks are not completed.",
            {"created_tasks": created_tasks, "completed_tasks": completed_tasks},
        ),
        _check(
            "file_changes",
            "Files changed",
            "file_changed" in types or bool(changed_files),
            "required",
            "File changes were recorded.",
            "No file changes were recorded.",
            {"changed_files_count": len(changed_files)},
        ),
        _check(
            "diff_available",
            "Diff is available",
            "diff_updated" in types or diff_exists or bool(diff_info.get("diff")),
            "required",
            "Diff evidence is available.",
            "No Diff evidence was found.",
            {"has_diff_patch": diff_exists, "diff_source": diff_info.get("source")},
        ),
        _check(
            "tests_finished",
            "Verification finished",
            has_verification,
            "recommended",
            "Verification evidence is available.",
            "No verification evidence was found.",
            {"has_test_finished": "test_finished" in types, "inferred_from_tool": has_verification and "test_finished" not in types},
        ),
        _check(
            "report_ready",
            "Delivery report is ready",
            has_report,
            "required",
            "A delivery report is available.",
            "No delivery report was found.",
            {"has_report_file": any((run_dir / name).exists() for name in ("report.md", "delivery.md", "delivery.json"))},
        ),
    ]

    if stages:
        checks.extend(
            [
                _check(
                    "execution_stages_terminal",
                    "Execution stages reached terminal states",
                    not non_terminal_stages,
                    "required",
                    "All planned stages reached completed, failed, or skipped.",
                    "Some planned stages are still pending or running.",
                    {
                        "stage_count": len(stages),
                        "non_terminal_stage_ids": [stage.get("id") for stage in non_terminal_stages],
                        "stage_statuses": stage_statuses,
                    },
                ),
                _check(
                    "required_stages_completed",
                    "Required stages completed",
                    session_status != "completed" or not required_incomplete_stages,
                    "required",
                    "All required stages completed.",
                    "A completed run still has required stages that did not complete.",
                    {
                        "required_incomplete_stage_ids": [
                            stage.get("id") for stage in required_incomplete_stages
                        ],
                    },
                ),
                _check(
                    "no_failed_stages",
                    "No failed execution stages",
                    not failed_stages,
                    "required",
                    "No planned stage failed.",
                    "One or more planned stages failed.",
                    {
                        "failed_stage_ids": [stage.get("id") for stage in failed_stages],
                        "failed_stage_titles": [stage.get("title") for stage in failed_stages],
                    },
                ),
                _check(
                    "stage_tool_evidence",
                    "Stage tool evidence recorded",
                    stage_evidence_count > 0,
                    "recommended",
                    "Tool evidence is attached to execution stages.",
                    "No tool evidence is attached to execution stages.",
                    {"stage_evidence_count": stage_evidence_count},
                ),
            ]
        )

    failed_count = sum(1 for item in checks if item["status"] == "failed")
    warning_count = sum(1 for item in checks if item["status"] == "warning")
    passed_count = sum(1 for item in checks if item["status"] == "passed")

    if failed_count:
        overall = "failed"
    elif warning_count:
        overall = "warning"
    else:
        overall = "passed"

    return {
        "thread_id": thread_id,
        "workspace_dir": str(workspace),
        "status": overall,
        "passed_count": passed_count,
        "warning_count": warning_count,
        "failed_count": failed_count,
        "checks": checks,
    }
