"""Workspace-scoped, auditable memory records for nanoCursor."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


MemoryScope = Literal["global", "workspace", "conversation", "run", "file", "rule"]
MemoryStatus = Literal["active", "disabled", "stale", "deleted"]
MemoryFreshness = Literal["fresh", "stale", "unknown"]
MemorySource = Literal["user", "rule_file", "system_summary", "run_evidence", "failure_recovery", "legacy"]

_STORE_LOCK = threading.RLock()
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|private[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
_PRIVACY_PATTERNS = [
    re.compile(r"(?i)\b(?:ssn|social security|身份证号|银行卡号)\b"),
]


class MemoryRecord(BaseModel):
    """One governed memory with explicit scope, provenance, and freshness."""

    id: str = Field(default_factory=lambda: f"mem_{uuid.uuid4().hex}")
    schema_version: int = 1
    scope: MemoryScope
    workspace_id: str
    conversation_id: str | None = None
    run_id: str | None = None
    file_path: str | None = None
    kind: str
    content: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    source: MemorySource = "user"
    source_ref: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    importance: int = Field(default=5, ge=0, le=10)
    status: MemoryStatus = "active"
    freshness: MemoryFreshness = "unknown"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    expires_at: float | None = None
    last_used_at: float | None = None
    use_count: int = 0
    evidence_refs: list[str] = Field(default_factory=list)
    file_fingerprint: str | None = None

    @model_validator(mode="after")
    def validate_scope_binding(self) -> "MemoryRecord":
        if self.scope == "conversation" and not self.conversation_id:
            raise ValueError("conversation scope requires conversation_id")
        if self.scope == "run" and not self.run_id:
            raise ValueError("run scope requires run_id")
        if self.scope in {"file", "rule"} and not self.file_path:
            raise ValueError(f"{self.scope} scope requires file_path")
        return self


def workspace_id_for(workspace_dir: str) -> str:
    root = str(Path(workspace_dir).resolve())
    return hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]


def memory_root(workspace_dir: str) -> Path:
    root = Path(workspace_dir).resolve() / ".nanocursor" / "memory"
    root.mkdir(parents=True, exist_ok=True)
    return root


def file_fingerprint(workspace_dir: str, file_path: str | None) -> str | None:
    if not file_path:
        return None
    workspace = Path(workspace_dir).resolve()
    candidate = (workspace / file_path).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    try:
        digest = hashlib.sha256()
        digest.update(str(candidate.relative_to(workspace)).encode("utf-8"))
        digest.update(candidate.read_bytes())
        return digest.hexdigest()
    except OSError:
        return None


def memory_safety_issues(content: str) -> list[str]:
    text = str(content or "")
    issues = []
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        issues.append("secret_or_credential")
    if any(pattern.search(text) for pattern in _PRIVACY_PATTERNS):
        issues.append("personal_privacy")
    return issues


def create_memory_record(
    workspace_dir: str,
    *,
    scope: MemoryScope,
    kind: str,
    content: str,
    source: MemorySource = "user",
    summary: str = "",
    tags: list[str] | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    file_path: str | None = None,
    source_ref: str | None = None,
    confidence: float = 0.7,
    importance: int = 5,
    evidence_refs: list[str] | None = None,
    automatic: bool = False,
) -> dict[str, Any]:
    """Create a governed record, rejecting unsafe automatic memory writes."""
    clean = str(content or "").strip()
    if not clean:
        raise ValueError("memory content cannot be empty")
    issues = memory_safety_issues(clean)
    if automatic and issues:
        raise ValueError(f"automatic memory rejected: {', '.join(issues)}")
    if automatic and source == "system_summary" and kind == "project_fact" and not evidence_refs:
        raise ValueError("automatic project facts require evidence_refs")

    fingerprint = file_fingerprint(workspace_dir, file_path)
    record = MemoryRecord(
        scope=scope,
        workspace_id=workspace_id_for(workspace_dir),
        conversation_id=conversation_id,
        run_id=run_id,
        file_path=_normalize_file_path(file_path),
        kind=str(kind or "workflow_note")[:100],
        content=clean[:8000],
        summary=(summary.strip() or _summary(clean))[:500],
        tags=_unique(tags or [])[:32],
        source=source,
        source_ref=source_ref,
        confidence=confidence,
        importance=importance,
        evidence_refs=_unique(evidence_refs or [])[:32],
        file_fingerprint=fingerprint,
        freshness="fresh" if fingerprint else "unknown",
    )
    records = _load_records(workspace_dir)
    records.append(record.model_dump(mode="json"))
    _save_records(workspace_dir, records)
    return record.model_dump(mode="json")


def list_memory_records(
    workspace_dir: str,
    *,
    scope: str | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    include_deleted: bool = False,
    limit: int = 200,
) -> list[dict[str, Any]]:
    workspace_id = workspace_id_for(workspace_dir)
    result = []
    for item in _load_records(workspace_dir):
        if item.get("workspace_id") != workspace_id:
            continue
        if not include_deleted and item.get("status") == "deleted":
            continue
        if scope and item.get("scope") != scope:
            continue
        if conversation_id and item.get("conversation_id") != conversation_id:
            continue
        if run_id and item.get("run_id") != run_id:
            continue
        if status and item.get("status") != status:
            continue
        result.append(item)
    result.sort(key=lambda item: (item.get("importance", 0), item.get("updated_at", 0)), reverse=True)
    return result[: max(0, min(limit, 1000))]


def get_memory_record(workspace_dir: str, memory_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in _load_records(workspace_dir) if item.get("id") == memory_id),
        None,
    )


def update_memory_record(workspace_dir: str, memory_id: str, **changes: Any) -> dict[str, Any] | None:
    """Update user-controllable fields while preserving scope bindings."""
    records = _load_records(workspace_dir)
    for index, item in enumerate(records):
        if item.get("id") != memory_id:
            continue
        allowed = {
            "content", "summary", "tags", "confidence", "importance", "status",
            "expires_at", "evidence_refs",
        }
        payload = {**item, **{key: value for key, value in changes.items() if key in allowed and value is not None}}
        payload["updated_at"] = time.time()
        if "content" in changes:
            payload["content"] = str(changes["content"]).strip()[:8000]
            payload["summary"] = str(changes.get("summary") or _summary(payload["content"]))[:500]
        record = MemoryRecord.model_validate(payload).model_dump(mode="json")
        records[index] = record
        _save_records(workspace_dir, records)
        return record
    return None


def delete_memory_record(workspace_dir: str, memory_id: str) -> bool:
    return update_memory_record(workspace_dir, memory_id, status="deleted") is not None


def mark_memory_used(workspace_dir: str, memory_ids: list[str]) -> None:
    ids = set(memory_ids)
    if not ids:
        return
    records = _load_records(workspace_dir)
    changed = False
    for item in records:
        if item.get("id") not in ids:
            continue
        item["last_used_at"] = time.time()
        item["use_count"] = int(item.get("use_count") or 0) + 1
        changed = True
    if changed:
        _save_records(workspace_dir, records)


def refresh_memory_freshness(workspace_dir: str) -> dict[str, Any]:
    """Refresh file-bound records and prevent stale facts from being selected."""
    records = _load_records(workspace_dir)
    stale_ids: list[str] = []
    refreshed = 0
    for item in records:
        if item.get("status") == "deleted" or not item.get("file_path"):
            continue
        current = file_fingerprint(workspace_dir, item.get("file_path"))
        previous = item.get("file_fingerprint")
        if current and previous == current:
            item["freshness"] = "fresh"
            continue
        if previous and current != previous:
            stale_ids.append(str(item.get("id")))
            item["freshness"] = "stale"
            if item.get("kind") == "failure_pattern":
                item["confidence"] = min(float(item.get("confidence") or 0.7), 0.45)
            else:
                item["status"] = "stale"
            item["updated_at"] = time.time()
            refreshed += 1
        elif not current:
            item["freshness"] = "stale"
            item["status"] = "stale"
            item["updated_at"] = time.time()
            stale_ids.append(str(item.get("id")))
            refreshed += 1
    if refreshed:
        _save_records(workspace_dir, records)
    return {"refreshed": refreshed, "stale_ids": stale_ids}


def extract_run_memory(workspace_dir: str, run_id: str) -> dict[str, Any]:
    """Create one evidence-backed run memory, skipping lightweight chat runs."""
    from src.api.services.event_store import get_event_store

    session = get_event_store().get_session(run_id, workspace_dir) or {}
    plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}
    if plan.get("strategy") == "lead_direct_reply":
        return {"created": False, "reason": "lead_direct_reply does not create run memory"}
    summary = str(session.get("execution_summary") or session.get("summary") or "").strip()
    if not summary:
        return {"created": False, "reason": "run has no execution summary"}
    existing = list_memory_records(workspace_dir, scope="run", run_id=run_id, include_deleted=True)
    if existing:
        return {"created": False, "reason": "run memory already exists", "memory": existing[0]}
    record = create_memory_record(
        workspace_dir,
        scope="run",
        run_id=run_id,
        conversation_id=session.get("conversation_id"),
        kind="failure_pattern" if session.get("status") == "failed" else "workflow_note",
        content=summary,
        source="failure_recovery" if session.get("status") == "failed" else "run_evidence",
        source_ref=f"run:{run_id}",
        confidence=0.9 if session.get("status") == "completed" else 0.65,
        importance=5,
        evidence_refs=[f"run:{run_id}"],
        automatic=True,
    )
    return {"created": True, "memory": record}


def _store_path(workspace_dir: str) -> Path:
    return memory_root(workspace_dir) / "records.json"


def _load_records(workspace_dir: str) -> list[dict[str, Any]]:
    path = _store_path(workspace_dir)
    with _STORE_LOCK:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
    return data if isinstance(data, list) else []


def _save_records(workspace_dir: str, records: list[dict[str, Any]]) -> None:
    path = _store_path(workspace_dir)
    with _STORE_LOCK:
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _summary(content: str) -> str:
    return " ".join(str(content or "").split())[:500]


def _normalize_file_path(file_path: str | None) -> str | None:
    return str(file_path).replace("\\", "/").lstrip("./") if file_path else None


def _unique(values: list[Any]) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
