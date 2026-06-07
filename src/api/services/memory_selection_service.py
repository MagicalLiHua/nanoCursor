"""Explainable, budgeted memory selection for ContextPack."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from pathlib import Path
from typing import Any

from src.api.services.memory_governance_service import (
    MemoryRecord,
    file_fingerprint,
    list_memory_records,
    mark_memory_used,
    memory_root,
    refresh_memory_freshness,
    workspace_id_for,
)


SOURCE_PRIORITY = {
    "rule_file": 1.0,
    "user": 0.95,
    "run_evidence": 0.9,
    "failure_recovery": 0.85,
    "system_summary": 0.7,
    "legacy": 0.55,
}
SCOPE_PRIORITY = {
    "rule": 1.0,
    "workspace": 0.9,
    "conversation": 0.85,
    "file": 0.85,
    "run": 0.65,
    "global": 0.6,
}
STOP_WORDS = {
    "and", "are", "for", "from", "into", "that", "the", "this", "use", "uses",
    "with", "your",
}


def select_memories(
    workspace_dir: str,
    *,
    prompt: str,
    conversation_id: str | None = None,
    run_id: str | None = None,
    selected_files: list[str] | None = None,
    active_task: dict[str, Any] | None = None,
    budget_tokens: int = 1200,
    persist_audit: bool = True,
) -> dict[str, Any]:
    """Select scoped memories with freshness filtering and deterministic scoring."""
    refresh_memory_freshness(workspace_dir)
    files = list(dict.fromkeys(str(path) for path in (selected_files or []) if path))
    query_text = " ".join([
        str(prompt or ""),
        str((active_task or {}).get("title") or ""),
        str((active_task or {}).get("goal") or ""),
        " ".join(files),
    ])
    query_terms = _terms(query_text)
    candidates = [
        *_governed_candidates(workspace_dir),
        *_rule_candidates(workspace_dir),
        *_conversation_candidate(workspace_dir, conversation_id),
        *_run_candidate(workspace_dir, run_id),
    ]
    eligible, omitted = _filter_candidates(candidates, conversation_id=conversation_id, run_id=run_id, files=files)
    ranked = []
    seen_content: set[str] = set()
    for item in eligible:
        digest = hashlib.sha256(str(item.get("content") or "").strip().lower().encode("utf-8")).hexdigest()
        if digest in seen_content:
            omitted.append(_omitted(item, "duplicate content"))
            continue
        seen_content.add(digest)
        score, reasons = _score_memory(item, query_terms=query_terms, selected_files=files, conversation_id=conversation_id, run_id=run_id)
        minimum_score = _minimum_selection_score(
            item,
            selected_files=files,
            conversation_id=conversation_id,
            run_id=run_id,
        )
        if score < minimum_score:
            omitted.append(
                _omitted(
                    item,
                    f"low relevance: score {score} < {minimum_score}",
                    score=score,
                    reasons=reasons,
                )
            )
            continue
        ranked.append({**item, "score": score, "reasons": reasons, "token_estimate": _token_estimate(item)})
    ranked.sort(key=lambda item: (item["score"], item.get("importance", 0)), reverse=True)

    selected = []
    used = 0
    for item in ranked:
        tokens = int(item.get("token_estimate") or 0)
        if used + tokens > budget_tokens:
            omitted.append(_omitted(item, "memory budget exhausted", score=item["score"], reasons=item["reasons"]))
            continue
        selected.append(_selection_item(item))
        used += tokens

    governed_ids = [item["id"] for item in selected if not str(item["id"]).startswith("transient:")]
    mark_memory_used(workspace_dir, governed_ids)
    result = {
        "selection_id": f"memsel_{uuid.uuid4().hex[:16]}",
        "selected": selected,
        "omitted": omitted[:100],
        "budget": {
            "requested_tokens": budget_tokens,
            "used_tokens_estimate": used,
            "selected_count": len(selected),
            "omitted_count": len(omitted),
        },
        "debug": {
            "candidate_count": len(candidates),
            "eligible_count": len(eligible),
            "query_terms": sorted(query_terms)[:30],
            "selected_files": files[:20],
        },
    }
    if persist_audit:
        _write_audit(workspace_dir, result)
    return result


def _governed_candidates(workspace_dir: str) -> list[dict[str, Any]]:
    return list_memory_records(workspace_dir, include_deleted=True, limit=1000)


def _rule_candidates(workspace_dir: str) -> list[dict[str, Any]]:
    workspace = Path(workspace_dir).resolve()
    paths = [workspace / "AGENTS.md", workspace / "CLAUDE.md"]
    cursor_rules = workspace / ".cursor" / "rules"
    if cursor_rules.is_dir():
        paths.extend(sorted(cursor_rules.glob("*.md")))
        paths.extend(sorted(cursor_rules.glob("*.mdc")))
    result = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")[:5000]
            relative = str(path.relative_to(workspace))
        except (OSError, ValueError):
            continue
        result.append(
            MemoryRecord(
                id=f"transient:rule:{relative}",
                scope="rule",
                workspace_id=workspace_id_for(workspace_dir),
                file_path=relative,
                kind="project_rule",
                content=content,
                summary=" ".join(content.split())[:500],
                source="rule_file",
                source_ref=relative,
                confidence=1.0,
                importance=10,
                freshness="fresh",
                file_fingerprint=file_fingerprint(workspace_dir, relative),
            ).model_dump(mode="json")
        )
    return result


def _conversation_candidate(workspace_dir: str, conversation_id: str | None) -> list[dict[str, Any]]:
    if not conversation_id:
        return []
    path = Path(workspace_dir).resolve() / ".nanocursor" / "conversations" / _safe_id(conversation_id) / "conversation.json"
    data = _read_json(path)
    summary = str(data.get("conversation_summary") or "") if data else ""
    if not summary:
        return []
    return [
        MemoryRecord(
            id=f"transient:conversation:{conversation_id}",
            scope="conversation",
            workspace_id=workspace_id_for(workspace_dir),
            conversation_id=conversation_id,
            kind="decision",
            content=summary,
            summary=" ".join(summary.split())[:500],
            source="system_summary",
            source_ref=str(path),
            confidence=0.75,
            importance=7,
        ).model_dump(mode="json")
    ]


def _run_candidate(workspace_dir: str, run_id: str | None) -> list[dict[str, Any]]:
    if not run_id:
        return []
    path = Path(workspace_dir).resolve() / ".nanocursor" / "runs" / _safe_id(run_id) / "session.json"
    data = _read_json(path)
    summary = str(data.get("execution_summary") or data.get("summary") or "") if data else ""
    if not summary:
        return []
    return [
        MemoryRecord(
            id=f"transient:run:{run_id}",
            scope="run",
            workspace_id=workspace_id_for(workspace_dir),
            conversation_id=data.get("conversation_id"),
            run_id=run_id,
            kind="workflow_note",
            content=summary,
            summary=" ".join(summary.split())[:500],
            source="run_evidence",
            source_ref=str(path),
            confidence=0.85,
            importance=6,
        ).model_dump(mode="json")
    ]


def _filter_candidates(
    candidates: list[dict[str, Any]],
    *,
    conversation_id: str | None,
    run_id: str | None,
    files: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    eligible, omitted = [], []
    for item in candidates:
        status = item.get("status")
        if status in {"disabled", "deleted", "stale"} or item.get("freshness") == "stale":
            omitted.append(_omitted(item, f"not selectable: {status or item.get('freshness')}"))
            continue
        if item.get("expires_at") and float(item["expires_at"]) < time.time():
            omitted.append(_omitted(item, "expired"))
            continue
        scope = item.get("scope")
        if scope == "conversation" and item.get("conversation_id") != conversation_id:
            omitted.append(_omitted(item, "conversation scope mismatch"))
            continue
        if scope == "run" and item.get("run_id") != run_id:
            omitted.append(_omitted(item, "run scope mismatch"))
            continue
        if scope == "file" and files and item.get("file_path") not in files:
            omitted.append(_omitted(item, "file not selected for current context"))
            continue
        eligible.append(item)
    return eligible, omitted


def _score_memory(
    item: dict[str, Any],
    *,
    query_terms: set[str],
    selected_files: list[str],
    conversation_id: str | None,
    run_id: str | None,
) -> tuple[float, list[str]]:
    content_terms = _terms(" ".join([
        str(item.get("summary") or ""),
        str(item.get("content") or ""),
        " ".join(str(tag) for tag in item.get("tags", []) if tag),
    ]))
    overlap = query_terms & content_terms
    keyword = min(len(overlap) / max(len(query_terms), 1), 1.0)
    scope_score = SCOPE_PRIORITY.get(str(item.get("scope")), 0.5)
    file_match = 1.0 if item.get("file_path") in selected_files else 0.0
    if item.get("scope") == "conversation" and item.get("conversation_id") == conversation_id:
        scope_score = 1.0
    if item.get("scope") == "run" and item.get("run_id") == run_id:
        scope_score = 1.0
    age_days = max((time.time() - float(item.get("updated_at") or time.time())) / 86400, 0)
    recency = math.exp(-age_days / 60)
    confidence = float(item.get("confidence") or 0)
    importance = float(item.get("importance") or 0) / 10
    source = SOURCE_PRIORITY.get(str(item.get("source")), 0.5)
    score = (
        0.30 * keyword
        + 0.16 * scope_score
        + 0.14 * file_match
        + 0.10 * recency
        + 0.10 * confidence
        + 0.08 * importance
        + 0.12 * source
    )
    reasons = []
    if overlap:
        reasons.append("matched terms: " + ", ".join(sorted(overlap)[:8]))
    reasons.append(f"{item.get('scope')} scope")
    reasons.append(f"source={item.get('source')}")
    if file_match:
        reasons.append(f"matched selected file: {item.get('file_path')}")
    if confidence >= 0.8:
        reasons.append("high confidence")
    if int(item.get("importance") or 0) >= 8:
        reasons.append("high importance")
    return round(score, 4), reasons


def _minimum_selection_score(
    item: dict[str, Any],
    *,
    selected_files: list[str],
    conversation_id: str | None,
    run_id: str | None,
) -> float:
    """Require broad memories to prove relevance while preserving strong local context."""
    scope = item.get("scope")
    if scope == "rule":
        return 0.18
    if scope == "conversation" and item.get("conversation_id") == conversation_id:
        return 0.40
    if scope == "run" and item.get("run_id") == run_id:
        return 0.40
    if scope == "file" and item.get("file_path") in selected_files:
        return 0.40
    if item.get("kind") == "failure_pattern":
        return 0.52
    if item.get("source") == "legacy":
        return 0.58
    return 0.55


def _selection_item(item: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: item.get(key)
        for key in (
            "id", "scope", "kind", "summary", "source", "source_ref",
            "confidence", "importance", "file_path", "conversation_id", "run_id",
            "tags", "score", "reasons", "token_estimate",
        )
        if item.get(key) not in (None, "", [])
    }
    if not result.get("summary"):
        result["summary"] = " ".join(str(item.get("content") or "").split())[:500]
    return result


def _omitted(item: dict[str, Any], reason: str, *, score: float | None = None, reasons: list[str] | None = None) -> dict[str, Any]:
    result = {
        "id": item.get("id"),
        "scope": item.get("scope"),
        "kind": item.get("kind"),
        "reason": reason,
    }
    if score is not None:
        result["score"] = score
    if reasons:
        result["reasons"] = reasons
    return result


def _token_estimate(item: dict[str, Any]) -> int:
    summary = str(item.get("summary") or "") or " ".join(str(item.get("content") or "").split())[:500]
    return max(8, len(summary) // 3)


def _terms(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", str(text or ""))
        if token.lower() not in STOP_WORDS
    }


def _write_audit(workspace_dir: str, result: dict[str, Any]) -> None:
    path = memory_root(workspace_dir) / "selections"
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{result['selection_id']}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _safe_id(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(raw or "")).strip("-")[:120] or "unknown"
