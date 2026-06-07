"""Evidence-based quality metrics for one persisted Agent run.

The service is deliberately read-only. It explains runtime quality from durable
session, event, loop-ledger, and ContextPack evidence without influencing the
run being measured.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.api.services.agent_loop_state_service import load_agent_loop_state
from src.api.services.event_store import get_event_store
from src.tools.tool_result import is_tool_error_output


TERMINAL_SUCCESS = {"completed"}
TERMINAL_FAILURE = {"failed", "cancelled", "interrupted"}
FILE_PATTERN = re.compile(r"(?<![\w/.-])([\w./-]+\.[A-Za-z0-9]{1,10})(?![\w/.-])")


def build_run_eval_metrics(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Build explainable loop/context/tool/recovery/memory metrics for one run."""
    workspace = Path(workspace_dir).resolve()
    workspace_str = str(workspace)
    store = get_event_store()
    session = store.get_session(thread_id, workspace_str)
    events = store.list_events(thread_id, workspace_str)
    if not session and not events:
        return {
            "thread_id": thread_id,
            "workspace_dir": workspace_str,
            "status": "not_found",
            "metrics": {},
            "score": None,
        }

    session = session or {}
    packs = _load_context_packs(thread_id, workspace)
    loop_state = load_agent_loop_state(thread_id, workspace_str)
    metrics = {
        "turn_count": _turn_count_metric(events, loop_state, session),
        "context_relevance": _context_relevance_metric(events, packs, workspace),
        "tool_execution_rate": _tool_execution_metric(events),
        "recovery_success_rate": _recovery_metric(session, packs, workspace_str),
        "memory_precision": _memory_precision_metric(packs),
        "approval_resolution_rate": _approval_metric(events),
    }
    applicable_scores = [
        float(metric["value"])
        for metric in metrics.values()
        if metric.get("value") is not None and metric.get("kind") == "rate"
    ]
    statuses = [metric.get("status") for metric in metrics.values()]
    overall_status = "failed" if "failed" in statuses else "warning" if "warning" in statuses else "passed"
    if not applicable_scores and all(status == "not_applicable" for status in statuses):
        overall_status = "not_applicable"
    return {
        "thread_id": thread_id,
        "workspace_dir": workspace_str,
        "status": str(session.get("status") or "unknown"),
        "strategy": _strategy(session),
        "conversation_id": session.get("conversation_id"),
        "original_thread_id": session.get("original_thread_id"),
        "metrics": metrics,
        "score": round(sum(applicable_scores) / len(applicable_scores), 3) if applicable_scores else None,
        "overall_status": overall_status,
        "evidence": {
            "event_count": len(events),
            "context_pack_count": len(packs),
            "has_loop_ledger": loop_state is not None,
        },
    }


def build_workspace_eval_metrics(workspace_dir: str, limit: int = 50) -> dict[str, Any]:
    """Aggregate recent run metrics into a trend summary."""
    workspace = Path(workspace_dir).resolve()
    runs_root = workspace / ".nanocursor" / "runs"
    if not runs_root.is_dir():
        return _empty_workspace_summary(str(workspace))

    sessions: list[tuple[float, str]] = []
    for run_dir in runs_root.iterdir():
        if not run_dir.is_dir():
            continue
        session = _read_json(run_dir / "session.json")
        if not session:
            continue
        thread_id = str(session.get("thread_id") or run_dir.name)
        updated_at = float(session.get("updated_at") or session.get("created_at") or 0)
        sessions.append((updated_at, thread_id))
    sessions.sort(reverse=True)
    runs = [build_run_eval_metrics(thread_id, str(workspace)) for _, thread_id in sessions[: max(1, min(limit, 200))]]

    metric_summary: dict[str, dict[str, Any]] = {}
    for name in (
        "context_relevance",
        "tool_execution_rate",
        "recovery_success_rate",
        "memory_precision",
        "approval_resolution_rate",
    ):
        values = [
            float(run["metrics"][name]["value"])
            for run in runs
            if run.get("metrics", {}).get(name, {}).get("value") is not None
        ]
        metric_summary[name] = {
            "applicable_runs": len(values),
            "average": round(sum(values) / len(values), 3) if values else None,
        }
    turn_counts = [
        int(run["metrics"]["turn_count"]["value"])
        for run in runs
        if run.get("metrics", {}).get("turn_count", {}).get("value") is not None
    ]
    return {
        "workspace_dir": str(workspace),
        "total_runs": len(runs),
        "completed_runs": sum(run.get("status") == "completed" for run in runs),
        "failed_runs": sum(run.get("status") in TERMINAL_FAILURE for run in runs),
        "avg_turn_count": round(sum(turn_counts) / len(turn_counts), 2) if turn_counts else None,
        "metrics": metric_summary,
        "runs": [
            {
                "thread_id": run.get("thread_id"),
                "status": run.get("status"),
                "strategy": run.get("strategy"),
                "score": run.get("score"),
                "overall_status": run.get("overall_status"),
            }
            for run in runs
        ],
    }


def _turn_count_metric(events: list[Any], loop_state: Any, session: dict[str, Any]) -> dict[str, Any]:
    turn_ids = {
        str(event.payload.get("turn_id"))
        for event in events
        if event.type == "loop_turn_finished" and isinstance(event.payload, dict) and event.payload.get("turn_id")
    }
    step_count = len(loop_state.steps) if loop_state else 0
    count = max(len(turn_ids), step_count)
    strategy = _strategy(session)
    expected_max = 2 if strategy == "lead_direct_reply" else 20
    status = "passed" if count <= expected_max else "failed"
    if count == 0:
        status = "warning" if strategy else "not_applicable"
    return {
        "kind": "count",
        "value": count,
        "status": status,
        "expected_max": expected_max,
        "reason": "Loop turns are read from the durable loop ledger and finished-turn events.",
        "evidence": {"finished_turn_ids": sorted(turn_ids), "ledger_steps": step_count},
    }


def _context_relevance_metric(events: list[Any], packs: list[dict[str, Any]], workspace: Path) -> dict[str, Any]:
    if not packs:
        return _rate_metric(None, "not_applicable", "No persisted ContextPack evidence.", {})
    selected = {
        _normalize_path(item.get("path"), workspace)
        for pack in packs
        for item in pack.get("selected_files", [])
        if isinstance(item, dict) and item.get("path")
    }
    selected.discard("")
    targets = _evidence_files(events, workspace)
    explained = [
        item
        for pack in packs
        for item in pack.get("selected_files", [])
        if isinstance(item, dict) and item.get("reasons")
    ]
    selected_items = [
        item
        for pack in packs
        for item in pack.get("selected_files", [])
        if isinstance(item, dict)
    ]
    if targets:
        overlap = selected & targets
        precision = len(overlap) / max(len(selected), 1)
        recall = len(overlap) / len(targets)
        value = 2 * precision * recall / max(precision + recall, 1e-9)
        reason = "F1 overlap between selected ContextPack files and files observed in actual run evidence."
        evidence = {
            "selected_files": sorted(selected),
            "evidence_files": sorted(targets),
            "overlap": sorted(overlap),
            "precision": round(precision, 3),
            "recall": round(recall, 3),
        }
    else:
        value = len(explained) / max(len(selected_items), 1)
        reason = "No file targets were produced; score reflects explainability of selected files."
        evidence = {"selected_files": sorted(selected), "explained_items": len(explained), "selected_items": len(selected_items)}
    return _rate_metric(value, _rate_status(value, warning=0.45, failed=0.2), reason, evidence)


def _tool_execution_metric(events: list[Any]) -> dict[str, Any]:
    calls = [event for event in events if event.type in {"tool_call_finished", "tool_call_failed"}]
    if not calls:
        return _rate_metric(None, "not_applicable", "This run did not call tools.", {"total": 0})
    successes = 0
    failures = []
    for event in calls:
        payload = event.payload if isinstance(event.payload, dict) else {}
        output = payload.get("output") or event.content
        explicit_ok = payload.get("ok")
        ok = event.type != "tool_call_failed" and (
            bool(explicit_ok) if explicit_ok is not None else not is_tool_error_output(output)
        )
        if ok:
            successes += 1
        else:
            failures.append({"tool": payload.get("tool"), "event_id": event.id})
    value = successes / len(calls)
    return _rate_metric(
        value,
        _rate_status(value, warning=0.8, failed=0.5),
        "Successful tool completions divided by all durable tool completion/failure events.",
        {"total": len(calls), "successes": successes, "failures": failures},
    )


def _recovery_metric(session: dict[str, Any], packs: list[dict[str, Any]], workspace_dir: str) -> dict[str, Any]:
    original_id = str(session.get("original_thread_id") or "")
    if not original_id:
        return _rate_metric(None, "not_applicable", "This run is not a retry/recovery run.", {})
    original = get_event_store().get_session(original_id, workspace_dir) or {}
    retry_source_hits = [
        pack.get("id")
        for pack in packs
        if (
            pack.get("recovery_context", {}).get("original_thread_id") == original_id
            or pack.get("context_debug", {}).get("failure_context", {}).get("retry_source", {}).get("original_thread_id") == original_id
        )
    ]
    completed = str(session.get("status") or "") in TERMINAL_SUCCESS
    original_failed = str(original.get("status") or session.get("original_status") or "") in TERMINAL_FAILURE
    context_hit = bool(retry_source_hits)
    value = 1.0 if completed and original_failed and context_hit else 0.0
    return _rate_metric(
        value,
        "passed" if value == 1.0 else "failed",
        "Recovery succeeds only when a failed original run completes and its failure evidence reaches retry context.",
        {
            "original_thread_id": original_id,
            "original_status": original.get("status") or session.get("original_status"),
            "retry_status": session.get("status"),
            "retry_context_pack_ids": retry_source_hits,
        },
    )


def _memory_precision_metric(packs: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        item
        for pack in packs
        for item in pack.get("selected_memories", [])
        if isinstance(item, dict)
    ]
    if not selected:
        return _rate_metric(None, "not_applicable", "No governed memories were selected.", {"selected": 0})
    relevant = []
    for item in selected:
        reasons = [str(reason) for reason in item.get("reasons", [])]
        local_scope = item.get("scope") in {"rule", "conversation", "run", "file"}
        matched = any(reason.startswith("matched terms:") or reason.startswith("matched selected file:") for reason in reasons)
        if local_scope or matched:
            relevant.append(item.get("id"))
    value = len(relevant) / len(selected)
    return _rate_metric(
        value,
        _rate_status(value, warning=0.75, failed=0.5),
        "Selected memories must have a strong local scope or an explicit request/file match.",
        {"selected": len(selected), "relevant_ids": relevant},
    )


def _approval_metric(events: list[Any]) -> dict[str, Any]:
    requested = [event for event in events if event.type in {"approval_requested", "tool_approval_required"}]
    if not requested:
        return _rate_metric(None, "not_applicable", "This run did not request approvals.", {"requested": 0})
    resolved = [event for event in events if event.type == "approval_resolved"]
    requested_ids = {
        str(event.payload.get("approval_id"))
        for event in requested
        if isinstance(event.payload, dict) and event.payload.get("approval_id")
    }
    resolved_ids = {
        str(event.payload.get("approval_id"))
        for event in resolved
        if isinstance(event.payload, dict) and event.payload.get("approval_id")
    }
    matched = len(requested_ids & resolved_ids) if requested_ids else min(len(resolved), len(requested))
    value = matched / (len(requested_ids) if requested_ids else len(requested))
    return _rate_metric(
        value,
        "passed" if value == 1.0 else "warning",
        "Approval requests are matched to durable resolution events by approval_id when available.",
        {
            "requested": len(requested),
            "resolved": len(resolved),
            "requested_ids": sorted(requested_ids),
            "resolved_ids": sorted(resolved_ids),
            "matched": matched,
        },
    )


def _evidence_files(events: list[Any], workspace: Path) -> set[str]:
    paths: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {"file_changed", "test_finished", "error", "tool_call_finished", "tool_call_failed", "diff_updated"}:
            for key in ("path", "file_path", "target"):
                if payload.get(key) and _looks_like_file(payload[key]):
                    paths.add(_normalize_path(payload[key], workspace))
            tool_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
            for key in ("path", "file_path", "target"):
                if tool_input.get(key) and _looks_like_file(tool_input[key]):
                    paths.add(_normalize_path(tool_input[key], workspace))
            for text in (event.title, event.content, payload.get("error"), payload.get("output")):
                paths.update(_normalize_path(match, workspace) for match in FILE_PATTERN.findall(str(text or "")))
    return {path for path in paths if path}


def _load_context_packs(thread_id: str, workspace: Path) -> list[dict[str, Any]]:
    context_dir = workspace / ".nanocursor" / "runs" / _safe_id(thread_id) / "context"
    candidates = list((context_dir / "packs").glob("*.json")) if (context_dir / "packs").is_dir() else []
    current = context_dir / "run_context_pack.json"
    if current.is_file():
        candidates.append(current)
    packs = []
    seen = set()
    for path in candidates:
        data = _read_json(path)
        pack_id = str(data.get("id") or path.stem) if data else ""
        if data and pack_id not in seen:
            seen.add(pack_id)
            packs.append(data)
    return packs


def _strategy(session: dict[str, Any]) -> str:
    plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
    return str(plan.get("strategy") or session.get("strategy") or "")


def _rate_metric(value: float | None, status: str, reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "rate",
        "value": round(value, 3) if value is not None else None,
        "status": status,
        "reason": reason,
        "evidence": evidence,
    }


def _rate_status(value: float, *, warning: float, failed: float) -> str:
    if value < failed:
        return "failed"
    if value < warning:
        return "warning"
    return "passed"


def _looks_like_file(value: Any) -> bool:
    text = str(value or "")
    return bool(FILE_PATTERN.search(text) or re.search(r"\.[A-Za-z0-9]{1,10}$", text))


def _normalize_path(value: Any, workspace: Path | None = None) -> str:
    text = str(value or "").replace("\\", "/")
    if workspace and Path(text).is_absolute():
        try:
            text = Path(text).resolve().relative_to(workspace.resolve()).as_posix()
        except (OSError, ValueError):
            pass
    while text.startswith("./"):
        text = text[2:]
    return text


def _safe_id(value: str) -> str:
    return str(value).replace("/", "_").replace("\\", "_")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _empty_workspace_summary(workspace_dir: str) -> dict[str, Any]:
    return {
        "workspace_dir": workspace_dir,
        "total_runs": 0,
        "completed_runs": 0,
        "failed_runs": 0,
        "avg_turn_count": None,
        "metrics": {},
        "runs": [],
    }
