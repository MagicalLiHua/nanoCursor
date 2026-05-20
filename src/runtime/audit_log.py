"""Audit log — append-only JSONL trail for every action that touches the workspace.

R5: Every action (file I/O, command execution, git op, MCP call, recovery action)
leaves an audit record under <workspace>/.nanocursor/runs/<thread_id>/audit.jsonl.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.infra import config as config_module


class AuditRecord(BaseModel):
    audit_id: str
    thread_id: str
    action_id: str = ""
    kind: str = ""                  # ActionKind value
    target: str = ""
    decision: str = ""              # allowed | denied | approved | auto_allowed
    result: str = ""                # success | failure | cancelled
    reason: str = ""
    duration_ms: int = 0
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0


# ---- Repository ----


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


def _run_dir(thread_id: str, workspace_dir: str | None = None) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    return _workspace(workspace_dir) / ".nanocursor" / "runs" / safe_id


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class AuditLogRepository:
    """Append-only audit trail. All records go to audit.jsonl."""

    def append(self, record: AuditRecord, workspace_dir: str | None = None) -> None:
        rd = _run_dir(record.thread_id, workspace_dir)
        _append_jsonl(rd / "audit.jsonl", record.model_dump())

    def list(self, thread_id: str, workspace_dir: str | None = None,
             limit: int = 100) -> list[AuditRecord]:
        rd = _run_dir(thread_id, workspace_dir)
        path = rd / "audit.jsonl"
        if not path.exists():
            return []
        records: list[AuditRecord] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            for line in lines[-limit:]:
                if line.strip():
                    records.append(AuditRecord(**json.loads(line)))
        except (json.JSONDecodeError, OSError, TypeError):
            return []
        return records

    def count(self, thread_id: str, workspace_dir: str | None = None) -> int:
        rd = _run_dir(thread_id, workspace_dir)
        path = rd / "audit.jsonl"
        if not path.exists():
            return 0
        try:
            return sum(1 for _ in path.read_text(encoding="utf-8").splitlines() if _.strip())
        except (OSError, UnicodeDecodeError):
            return 0


_audit_repo: AuditLogRepository | None = None


def get_audit_repo() -> AuditLogRepository:
    global _audit_repo
    if _audit_repo is None:
        _audit_repo = AuditLogRepository()
    return _audit_repo
