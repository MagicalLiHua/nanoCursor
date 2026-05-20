"""Delivery contract service — build, persist, and render the unified run result.

Every terminal run (completed/failed/cancelled) gets a DeliveryContract written
as delivery.json + delivery.md under <workspace>/.nanocursor/runs/<thread_id>/.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.api.services.diff_service import get_run_diff
from src.api.services.event_store import get_event_store
from src.api.services.quality_service import build_quality_gate
from src.api.services.report_service import build_delivery_report
from src.api.services.score_service import build_delivery_score
from src.infra import config as config_module

# Lazy import to avoid circular deps
def _load_change_set_cached(thread_id: str, workspace_dir: str | None = None) -> Any | None:
    from src.api.services.change_service import load_change_set
    return load_change_set(thread_id, workspace_dir)
from src.runtime.delivery_contract import (
    DeliveryContract,
    DeliveryFileChange,
    DeliveryStatus,
    DeliveryVerification,
)


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


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _classify_risk(file_path: str, change_type: str, additions: int, deletions: int) -> str:
    """Rule-based file change risk classification."""
    path_lower = file_path.lower()

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


def build_delivery_contract(thread_id: str, workspace_dir: str | None = None) -> DeliveryContract:
    """Assemble a DeliveryContract from all existing run data sources."""
    ws = _workspace(workspace_dir)
    ws_str = str(ws)
    store = get_event_store()
    session = store.get_session(thread_id, ws_str)
    events = store.list_events(thread_id, ws_str)

    # --- status and objective ---
    session_status = session.get("status", "unknown") if session else "unknown"
    prompt = (session.get("prompt") or "") if session else ""

    if session_status == "completed":
        status = DeliveryStatus.READY
    elif session_status == "failed":
        status = DeliveryStatus.FAILED
    elif session_status == "cancelled":
        status = DeliveryStatus.BLOCKED
    else:
        status = DeliveryStatus.DRAFT

    # --- plan (from session execution_plan) ---
    plan: list[dict[str, Any]] = []
    if session:
        ep = session.get("execution_plan")
        if isinstance(ep, dict):
            stages = ep.get("stages")
            if isinstance(stages, list):
                plan = [s for s in stages if isinstance(s, dict)]

    # --- changed files (from changes.json when available, else diff service) ---
    cs = _load_change_set_cached(thread_id, ws_str)
    if cs is not None and cs.files:
        changed_files = [
            DeliveryFileChange(
                path=f.path,
                change_type=f.change_type,
                additions=f.additions,
                deletions=f.deletions,
                summary=f.summary,
                risk=f.risk,
            )
            for f in cs.files
        ]
    else:
        diff_info = get_run_diff(thread_id, ws_str)
        raw_files: list[dict[str, Any]] = diff_info.get("changed_files", [])
        changed_files = []
        for f in raw_files:
            path = str(f.get("path", ""))
            ct = str(f.get("change_type", "modified"))
            changed_files.append(
                DeliveryFileChange(
                    path=path,
                    change_type=ct,
                    additions=0,
                    deletions=0,
                    summary="",
                    risk=_classify_risk(path, ct, 0, 0),
                )
            )

    # --- verifications (from quality gate + events) ---
    quality = build_quality_gate(thread_id, ws_str)
    verifications: list[DeliveryVerification] = []

    test_events = [e for e in events if e.type == "test_finished"]
    for te in test_events[-5:]:
        payload = te.payload if isinstance(te.payload, dict) else {}
        verifications.append(
            DeliveryVerification(
                command=payload.get("command", te.title),
                exit_code=payload.get("exit_code"),
                status=payload.get("status", "passed"),
                stdout_tail=str(payload.get("stdout_tail", ""))[:500],
                stderr_tail=str(payload.get("stderr_tail", ""))[:500],
                duration_ms=payload.get("duration_ms", 0),
            )
        )

    # fallback: derive from quality checks
    if not verifications:
        for check in quality.get("checks", []):
            if check.get("id") in ("tests_finished", "run_completed", "no_runtime_errors"):
                verifications.append(
                    DeliveryVerification(
                        command=check.get("label", ""),
                        status=check.get("status", "not_run"),
                        stdout_tail=check.get("detail", ""),
                    )
                )

    failed_verifications = [
        v for v in verifications
        if str(v.status).lower() in {"failed", "error"}
        or (v.exit_code is not None and v.exit_code != 0)
    ]
    if status == DeliveryStatus.READY and failed_verifications:
        status = DeliveryStatus.BLOCKED

    # --- risks ---
    report = build_delivery_report(thread_id, ws_str)
    raw_risks: list[dict[str, Any]] = report.get("risks", [])
    risks: list[dict[str, Any]] = []
    if isinstance(raw_risks, list):
        for r in raw_risks:
            if isinstance(r, dict):
                risks.append(r)
            else:
                risks.append({"description": str(r)})

    # add high-risk files
    for cf in changed_files:
        if cf.risk == "high":
            risks.append({"description": f"High-risk file change: {cf.path} ({cf.change_type})"})
    for verification in failed_verifications:
        risks.append({
            "description": f"Verification failed: {verification.command or 'unknown command'}",
            "exit_code": verification.exit_code,
        })

    # --- open questions & next actions ---
    open_questions: list[str] = []
    next_actions: list[str] = []

    if status == DeliveryStatus.FAILED:
        error_events = [e for e in events if e.type == "error"]
        if error_events:
            open_questions.append(f"Run failed with {len(error_events)} error(s). Review failure details.")
        next_actions.append("审阅失败详情并决定是否重试")
    elif status == DeliveryStatus.BLOCKED:
        if failed_verifications:
            open_questions.append(f"{len(failed_verifications)} 个验证步骤失败，需要修复后再交付")
            next_actions.append("先修复失败的测试/验证，再重新生成交付契约")
        else:
            next_actions.append("运行已取消，可重新发起或检查取消原因")
    elif status == DeliveryStatus.READY:
        high_risk_count = sum(1 for cf in changed_files if cf.risk == "high")
        if high_risk_count:
            open_questions.append(f"{high_risk_count} 个高风险文件变更需要人工复核")
            next_actions.append(f"人工复核 {high_risk_count} 个高风险文件变更")
        if not verifications or all(v.status == "not_run" for v in verifications):
            next_actions.append("建议运行测试/构建验证变更")
        next_actions.append("建议人工点击核心流程再验收一次")

    # --- summary ---
    summary = report.get("summary", "")

    # --- score ---
    score_info: dict[str, Any] = {}
    try:
        score_info = build_delivery_score(thread_id, ws_str)
    except Exception:
        pass

    return DeliveryContract(
        thread_id=thread_id,
        workspace_dir=ws_str,
        status=status,
        objective=prompt,
        summary=str(summary),
        plan=plan,
        changed_files=changed_files,
        verifications=verifications,
        risks=risks,
        open_questions=open_questions,
        next_actions=next_actions,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def save_delivery_contract(contract: DeliveryContract) -> Path:
    """Persist delivery.json via atomic write. Returns the file path."""
    rd = _run_dir(contract.thread_id, contract.workspace_dir)
    path = rd / "delivery.json"
    _write_json_atomic(path, contract.model_dump())
    return path


def load_delivery_contract(thread_id: str, workspace_dir: str | None = None) -> DeliveryContract | None:
    """Load a previously saved delivery contract, or None if missing/corrupt."""
    rd = _run_dir(thread_id, workspace_dir)
    path = rd / "delivery.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return DeliveryContract(**data)
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def render_delivery_markdown(contract: DeliveryContract) -> str:
    """Render a DeliveryContract as a human-readable Markdown document."""
    lines = [
        "# nanoCursor Delivery Report",
        "",
        f"**Thread:** `{contract.thread_id}`",
        f"**Workspace:** `{contract.workspace_dir}`",
        f"**Status:** `{contract.status.value}`",
        f"**Generated:** {contract.generated_at}",
        "",
        "## Objective",
        "",
        contract.objective or "(no objective recorded)",
        "",
        "## Summary",
        "",
        contract.summary or "(no summary)",
        "",
    ]

    # Plan
    if contract.plan:
        lines.extend(["## Execution Plan", ""])
        lines.extend(["| Stage | Owner | Status |", "|---|---|---|"])
        for stage in contract.plan:
            lines.append(
                f"| {stage.get('title', stage.get('id', '-'))} "
                f"| {stage.get('owner', '-')} "
                f"| {stage.get('status', 'pending')} |"
            )
        lines.append("")

    # Changed files
    lines.extend(["## Changed Files", ""])
    if contract.changed_files:
        lines.extend(["| File | Change | Risk |", "|---|---|---|"])
        for cf in contract.changed_files:
            lines.append(f"| {cf.path} | {cf.change_type} | {cf.risk} |")
    else:
        lines.append("- No files changed.")
    lines.append("")

    # Verifications
    lines.extend(["## Verifications", ""])
    if contract.verifications:
        lines.extend(["| Command | Status | Exit | Duration |", "|---|---|---|---|"])
        for v in contract.verifications:
            lines.append(
                f"| {v.command} | {v.status} | {v.exit_code or '-'} | {v.duration_ms}ms |"
            )
    else:
        lines.append("- No verifications recorded.")
    lines.append("")

    # Risks
    lines.extend(["## Risks", ""])
    if contract.risks:
        for r in contract.risks:
            desc = r.get("description", str(r))
            lines.append(f"- {desc}")
    else:
        lines.append("- No risks identified.")
    lines.append("")

    # Open questions
    lines.extend(["## Open Questions", ""])
    if contract.open_questions:
        for q in contract.open_questions:
            lines.append(f"- {q}")
    else:
        lines.append("- None.")
    lines.append("")

    # Next actions
    lines.extend(["## Next Actions", ""])
    if contract.next_actions:
        for a in contract.next_actions:
            lines.append(f"- {a}")
    else:
        lines.append("- None.")
    lines.append("")

    return "\n".join(lines)


def save_delivery_markdown(contract: DeliveryContract) -> Path:
    """Render and persist delivery.md. Returns the file path."""
    rd = _run_dir(contract.thread_id, contract.workspace_dir)
    path = rd / "delivery.md"
    _write_text_atomic(path, render_delivery_markdown(contract))
    return path


def finalize_delivery(thread_id: str, workspace_dir: str | None = None, force: bool = False) -> DeliveryContract | None:
    """Build and persist the delivery contract for a run.

    Only persists when the run is in a terminal state or force=True.
    """
    ws_str = str(_workspace(workspace_dir))
    store = get_event_store()
    session = store.get_session(thread_id, ws_str)

    if not session and not force:
        return None

    session_status = session.get("status", "unknown") if session else "unknown"
    terminal_states = {"completed", "failed", "cancelled", "interrupted"}
    if session_status not in terminal_states and not force:
        return None

    contract = build_delivery_contract(thread_id, ws_str)
    save_delivery_contract(contract)
    save_delivery_markdown(contract)
    return contract


def regenerate_delivery(thread_id: str, workspace_dir: str | None = None, include_markdown: bool = True) -> DeliveryContract:
    """Force-regenerate the delivery contract from current run data."""
    contract = build_delivery_contract(thread_id, workspace_dir)
    save_delivery_contract(contract)
    if include_markdown:
        save_delivery_markdown(contract)
    return contract
