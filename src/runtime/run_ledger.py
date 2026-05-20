"""Run ledger — unified persistence for steps, tool calls, and run metadata.

Every run writes tool calls to tools.jsonl and stage/step changes to steps.json
under <workspace>/.nanocursor/runs/<thread_id>/. The ledger provides a single
query surface that survives restarts.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.infra import config as config_module


# ---------------------------------------------------------------------------
# Record models
# ---------------------------------------------------------------------------


class ToolCallRecord(BaseModel):
    call_id: str
    thread_id: str
    step_id: str = ""
    tool_name: str
    input_json: str = "{}"
    output_tail: str = ""
    status: str = "started"  # started | completed | failed | blocked
    started_at: float = 0.0
    completed_at: float = 0.0
    approval_id: str = ""


class StepRecord(BaseModel):
    step_id: str
    thread_id: str
    title: str
    owner: str = ""
    status: str = "pending"  # pending | running | completed | failed | skipped
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str = ""


class RunLedger(BaseModel):
    thread_id: str
    workspace_dir: str
    status: str = "unknown"
    mode: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float = 0.0
    steps: list[StepRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    approval_count: int = 0
    delivery_status: str = ""
    changes_status: str = ""


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_dir(thread_id: str, workspace_dir: str | None = None) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return _workspace(workspace_dir) / ".nanocursor" / "runs" / safe_id


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        return []
    return records


class RunLedgerRepository:
    """Unified persistence for run steps, tool calls, and metadata.

    All writes go through this repository. Reads can reconstruct the full
    run timeline from persisted files alone — no in-memory state needed.
    """

    # ---- tool calls (JSONL) ----

    def append_tool_call(
        self, thread_id: str, record: ToolCallRecord, workspace_dir: str | None = None,
    ) -> None:
        rd = _run_dir(thread_id, workspace_dir)
        _append_jsonl(rd / "tools.jsonl", record.model_dump())

    def get_tool_calls(
        self, thread_id: str, workspace_dir: str | None = None,
    ) -> list[ToolCallRecord]:
        rd = _run_dir(thread_id, workspace_dir)
        raw = _read_jsonl(rd / "tools.jsonl")
        return [ToolCallRecord(**r) for r in raw]

    # ---- steps (JSON) ----

    def write_steps(
        self, thread_id: str, steps: list[StepRecord], workspace_dir: str | None = None,
    ) -> None:
        rd = _run_dir(thread_id, workspace_dir)
        data = [s.model_dump() for s in steps]
        _write_json_atomic(rd / "steps.json", data)

    def get_steps(
        self, thread_id: str, workspace_dir: str | None = None,
    ) -> list[StepRecord]:
        rd = _run_dir(thread_id, workspace_dir)
        path = rd / "steps.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [StepRecord(**item) for item in data]
        except (json.JSONDecodeError, OSError, TypeError):
            return []

    # ---- ledger (aggregate) ----

    def build_ledger(
        self, thread_id: str, workspace_dir: str | None = None,
    ) -> RunLedger | None:
        """Build a unified ledger view from all persisted data sources."""
        rd = _run_dir(thread_id, workspace_dir)

        # Session
        session_path = rd / "session.json"
        session: dict[str, Any] = {}
        if session_path.exists():
            try:
                session = json.loads(session_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        if not session:
            return None

        # Steps
        steps = self.get_steps(thread_id, workspace_dir)

        # Tool calls
        tool_calls = self.get_tool_calls(thread_id, workspace_dir)

        # Approval count
        approvals_dir = rd / "approvals"
        approval_count = 0
        if approvals_dir.is_dir():
            approval_count = len(list(approvals_dir.glob("*.json")))

        # Delivery status
        delivery_status = ""
        delivery_path = rd / "delivery.json"
        if delivery_path.exists():
            try:
                dd = json.loads(delivery_path.read_text(encoding="utf-8"))
                delivery_status = dd.get("status", "")
            except (json.JSONDecodeError, OSError):
                pass

        # Changes status
        changes_status = ""
        changes_path = rd / "changes.json"
        if changes_path.exists():
            try:
                cd = json.loads(changes_path.read_text(encoding="utf-8"))
                changes_status = cd.get("status", "")
            except (json.JSONDecodeError, OSError):
                pass

        return RunLedger(
            thread_id=thread_id,
            workspace_dir=session.get("workspace_dir", str(_workspace(workspace_dir))),
            status=session.get("status", "unknown"),
            mode=session.get("mode", ""),
            created_at=session.get("created_at", 0.0),
            updated_at=session.get("updated_at", 0.0),
            completed_at=session.get("completed_at", 0.0),
            steps=steps,
            tool_calls=tool_calls,
            approval_count=approval_count,
            delivery_status=delivery_status,
            changes_status=changes_status,
        )


# Module-level singleton
_ledger_repo: RunLedgerRepository | None = None


def get_ledger_repo() -> RunLedgerRepository:
    global _ledger_repo
    if _ledger_repo is None:
        _ledger_repo = RunLedgerRepository()
    return _ledger_repo
