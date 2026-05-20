"""Tool approval service — persist, poll, resolve approval decisions.

Approvals are stored as individual JSON files under
``.nanocursor/runs/{thread_id}/approvals/{decision_id}.json``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from src.infra import config as config_module


DEFAULT_APPROVAL_TIMEOUT_SECONDS = 120.0
RESOLVED_APPROVAL_STATUSES = frozenset({"approved", "rejected"})


def _approvals_dir(thread_id: str, workspace_dir: str) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    d = root / ".nanocursor" / "runs" / thread_id.replace("/", "_") / "approvals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _approval_path(thread_id: str, decision_id: str, workspace_dir: str) -> Path:
    return _approvals_dir(thread_id, workspace_dir) / f"{decision_id}.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _decision_value(decision: Any, key: str, default: Any = "") -> Any:
    if hasattr(decision, key):
        return getattr(decision, key)
    if isinstance(decision, dict):
        return decision.get(key, default)
    return default


def _reject_approval(
    path: Path,
    existing: dict[str, Any] | None,
    *,
    decision_id: str,
    tool: str,
    reason: str,
) -> dict[str, Any]:
    now = time.time()
    data = dict(existing or {})
    data.update({
        "decision_id": decision_id,
        "tool": data.get("tool") or tool,
        "status": "rejected",
        "reason": reason,
        "resolved_at": now,
        "updated_at": now,
    })
    _write_json_atomic(path, data)
    return data


def _expire_if_needed(path: Path, data: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    if data.get("status") != "pending":
        return data
    expires_at = data.get("expires_at")
    if not expires_at:
        return data
    current = now or time.time()
    if float(expires_at) > current:
        return data
    return _reject_approval(
        path,
        data,
        decision_id=str(data.get("decision_id", path.stem)),
        tool=str(data.get("tool", "")),
        reason="审批超时，自动拒绝。",
    )


def create_tool_approval(
    thread_id: str,
    decision: Any,
    workspace_dir: str | None = None,
    timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Persist a pending approval to disk and return its dict."""
    ws = workspace_dir or config_module.WORKSPACE_DIR
    d = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
    d.setdefault("decision_id", f"approval_{id(decision):x}")
    d.setdefault("status", "pending")
    now = time.time()
    d.setdefault("thread_id", thread_id)
    d.setdefault("created_at", now)
    d["updated_at"] = now
    d.setdefault("expires_at", now + timeout_seconds)

    path = _approval_path(thread_id, d["decision_id"], ws)
    _write_json_atomic(path, d)
    return d


def get_tool_approval(
    thread_id: str,
    decision_id: str,
    workspace_dir: str | None = None,
) -> dict[str, Any] | None:
    """Return one approval record, expiring it first when needed."""
    ws = workspace_dir or config_module.WORKSPACE_DIR
    path = _approval_path(thread_id, decision_id, ws)
    if not path.exists():
        return None
    data = _read_json(path)
    if data is None:
        return None
    return _expire_if_needed(path, data)


def get_pending_approvals(
    thread_id: str,
    workspace_dir: str | None = None,
) -> list[dict[str, Any]]:
    """Return all still-pending approval decisions for a run.

    Stale pending records are rejected on read so the frontend never sees an
    indefinitely pending approval after its timeout window has elapsed.
    """
    ws = workspace_dir or config_module.WORKSPACE_DIR
    ad = _approvals_dir(thread_id, ws)
    if not ad.exists():
        return []

    results: list[dict[str, Any]] = []
    for f in sorted(ad.glob("*.json")):
        data = _read_json(f)
        if data is None:
            continue
        data = _expire_if_needed(f, data)
        if data.get("status") == "pending":
            results.append(data)
    return results


def resolve_tool_approval(
    thread_id: str,
    decision_id: str,
    approved: bool,
    comment: str = "",
    workspace_dir: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a pending approval. Returns the updated record, or None if not found."""
    ws = workspace_dir or config_module.WORKSPACE_DIR
    path = _approval_path(thread_id, decision_id, ws)
    if not path.exists():
        return None

    data = _read_json(path)
    if data is None:
        return None
    data = _expire_if_needed(path, data)

    if data.get("status") != "pending":
        return data

    now = time.time()
    data["status"] = "approved" if approved else "rejected"
    data["resolved_at"] = now
    data["updated_at"] = now
    data["comment"] = comment
    if not approved and not data.get("reason"):
        data["reason"] = "用户拒绝执行该工具。"
    _write_json_atomic(path, data)
    return data


def wait_for_approval(
    thread_id: str,
    decision: Any,
    timeout_seconds: float = 120.0,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Block until the approval is resolved or timeout.

    Returns the resolved approval dict with ``status`` of "approved" or "rejected".
    On timeout, returns a dict with ``status: "rejected"`` and ``reason: "timeout"``.
    """
    ws = workspace_dir or config_module.WORKSPACE_DIR
    decision_id = _decision_value(decision, "decision_id", "")
    path = _approval_path(thread_id, decision_id, ws)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        data = get_tool_approval(thread_id, decision_id, ws)
        if data and data.get("status") in RESOLVED_APPROVAL_STATUSES:
            return data
        time.sleep(0.5)

    return _reject_approval(
        path,
        get_tool_approval(thread_id, decision_id, ws),
        decision_id=decision_id,
        tool=str(_decision_value(decision, "tool", "")),
        reason="审批超时，自动拒绝。",
    )


async def wait_for_approval_async(
    thread_id: str,
    decision: Any,
    timeout_seconds: float = 120.0,
    workspace_dir: str | None = None,
    *,
    poll_interval_seconds: float = 0.5,
    should_abort: Any | None = None,
) -> dict[str, Any]:
    """Wait for approval without blocking the active event loop.

    ``should_abort`` is an optional callable used by run lifecycle code to stop
    waiting when the run is cancelled, failed, or interrupted.
    """
    ws = workspace_dir or config_module.WORKSPACE_DIR
    decision_id = _decision_value(decision, "decision_id", "")
    path = _approval_path(thread_id, decision_id, ws)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if should_abort and should_abort():
            return _reject_approval(
                path,
                get_tool_approval(thread_id, decision_id, ws),
                decision_id=decision_id,
                tool=str(_decision_value(decision, "tool", "")),
                reason="Run 已取消或中断，审批自动拒绝。",
            )

        data = get_tool_approval(thread_id, decision_id, ws)
        if data and data.get("status") in RESOLVED_APPROVAL_STATUSES:
            return data
        await asyncio.sleep(poll_interval_seconds)

    return _reject_approval(
        path,
        get_tool_approval(thread_id, decision_id, ws),
        decision_id=decision_id,
        tool=str(_decision_value(decision, "tool", "")),
        reason="审批超时，自动拒绝。",
    )
