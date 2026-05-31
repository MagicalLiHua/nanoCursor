"""Build structured ContextPack from workspace state."""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from src.agent.context_pack import ContextPack
from src.infra import config as config_module
from src.indexer.indexer import get_project_index


def _workspace(workspace_dir: str | None = None) -> Path:
    return Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()


_STRATEGY_TOKEN_BUDGETS = {
    "analysis_only": 15000,
    "docs_only": 8000,
    "small_patch": 10000,
    "bug_fix": 12000,
    "refactor": 15000,
    "feature_delivery": 12000,
}


def _strategy_token_budget(strategy: str) -> int:
    """Return the token budget for a given strategy."""
    return _STRATEGY_TOKEN_BUDGETS.get(strategy, 12000)


def build_context_pack(
    prompt: str = "",
    team: list[dict[str, Any]] | None = None,
    workspace_dir: str | None = None,
    execution_plan: dict[str, Any] | None = None,
    conversation_id: str | None = None,
    thread_id: str | None = None,
) -> ContextPack:
    """Build a structured context pack from workspace and execution state."""
    workspace = _workspace(workspace_dir)
    pack = ContextPack()

    # Task summary
    pack.task_summary = (prompt or "")[:200]
    pack.conversation_summary = _conversation_summary(workspace, conversation_id)
    pack.execution_summary = _execution_summary(workspace, thread_id)

    # Workspace summary
    pack.workspace_summary = _workspace_summary(workspace)

    index_data = _project_index_summary(workspace)
    recent_change_list = _recent_changes(workspace, index_data)
    conversation_symbols = _extract_conversation_symbols(
        pack.conversation_summary, pack.execution_summary
    )
    pack.relevant_files = _select_relevant_files(
        prompt, index_data, execution_plan,
        recent_changes=set(recent_change_list),
        conversation_symbols=conversation_symbols,
    )
    pack.recent_changes = recent_change_list
    pack.file_outlines = _file_outlines(index_data, pack.relevant_files)
    pack.symbols = _symbol_names(pack.file_outlines)

    # Recent failures from recovery
    from src.api.services.recovery_service import build_recovery_center
    recovery = build_recovery_center(None, str(workspace))
    pack.recent_failures = [
        {
            "category": r.get("evidence", {}).get("failure_category", "unknown"),
            "summary": r.get("title", ""),
            "detail": r.get("detail", ""),
        }
        for r in recovery.get("risks", [])[:5]
    ]

    # User preferences
    try:
        from src.api.services.preference_service import build_memory_profile
        profile = build_memory_profile(str(workspace))
        pack.user_preferences = [
            b.get("label", "") for b in profile.get("buckets", [])[:3]
            if b.get("label")
        ]
    except Exception:
        pack.user_preferences = []

    # Selected skills
    if execution_plan:
        capabilities = execution_plan.get("capabilities", []) or []
        pack.selected_skills = [
            c for c in capabilities if isinstance(c, str) and c.startswith("skill.")
        ]
        pack.current_plan = [
            {
                "id": str(stage.get("id", "")),
                "title": str(stage.get("title", "")),
                "description": str(stage.get("description", ""))[:240],
                "owner": str(stage.get("owner", "")),
            }
            for stage in execution_plan.get("stages", [])[:8]
            if isinstance(stage, dict)
        ]

    # Token budget (strategy-dependent)
    strategy = (execution_plan or {}).get("strategy", "feature_delivery")
    pack.token_budget = {
        "max_tokens": _strategy_token_budget(strategy),
        "used_tokens_estimate": pack.estimate_tokens(),
    }

    return pack


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _conversation_summary(workspace: Path, conversation_id: str | None) -> str:
    if not conversation_id:
        return ""
    path = workspace / ".nanocursor" / "conversations" / _safe_id(conversation_id) / "conversation.json"
    data = _read_json(path) or {}
    summary = data.get("conversation_summary")
    if isinstance(summary, str):
        return summary[:1200]
    records = data.get("run_records") if isinstance(data.get("run_records"), list) else []
    parts = []
    for record in records[-5:]:
        if not isinstance(record, dict):
            continue
        parts.append(
            f"Run#{record.get('run_index', '?')} {record.get('status', 'unknown')}: "
            f"{record.get('prompt', '')[:80]} -> {record.get('summary', '')[:160]}"
        )
    return "；".join(parts)[:1200]


def _execution_summary(workspace: Path, thread_id: str | None) -> str:
    if not thread_id:
        return ""
    session = _read_json(workspace / ".nanocursor" / "runs" / _safe_id(thread_id) / "session.json") or {}
    summary = session.get("execution_summary") or session.get("summary")
    if isinstance(summary, str):
        return summary[:1200]
    status = str(session.get("status", "") or "")
    prompt = str(session.get("prompt", "") or "")
    return f"{status}: {prompt[:240]}".strip(": ")


def _safe_id(raw: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(raw).strip()).strip("-")
    return safe[:120] or "unknown"


def _project_index_summary(workspace: Path) -> dict[str, Any]:
    try:
        idx = get_project_index(workspace)
        if idx.workspace != workspace:
            from src.indexer.indexer import reset_index
            reset_index()
            idx = get_project_index(workspace)
        idx.update()
        summary = idx.summary()
        summary["entries"] = {
            rel: {
                "path": entry.path,
                "role": entry.role,
                "language": entry.language,
                "symbols": entry.symbols,
                "imports": entry.imports,
                "mtime": entry.mtime,
                "size": entry.size,
                "loc": entry.loc,
                "routes": entry.routes or [],
                "call_graph": entry.call_graph or {},
            }
            for rel, entry in idx.entries.items()
        }
        return summary
    except Exception:
        return _read_project_index(workspace)


def _prompt_terms(prompt: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}", prompt or "")
    }


def _build_haystack(entry: dict[str, Any]) -> str:
    """Build a searchable text blob from an index entry."""
    parts = [
        entry.get("path", ""),
        entry.get("role", ""),
        entry.get("language", ""),
        " ".join(
            str(sym.get("name", ""))
            for sym in entry.get("symbols", [])
            if isinstance(sym, dict)
        ),
        " ".join(entry.get("imports", [])),
    ]
    return " ".join(parts).lower()


def _score_file(
    entry: dict[str, Any],
    prompt_terms: set[str],
    recent_changes: set[str],
    conversation_symbols: set[str],
    all_entries: dict[str, Any],
) -> float:
    """Multi-dimensional file relevance scoring."""
    score = 0.0

    # \u2500\u2500 Dimension 1: Semantic match with TF-IDF weighting \u2500\u2500
    haystack = _build_haystack(entry)
    doc_count = len(all_entries)
    for term in prompt_terms:
        if term in haystack:
            containing = sum(
                1 for e in all_entries.values() if term in _build_haystack(e)
            )
            idf = math.log((doc_count + 1) / (containing + 1)) + 1
            score += idf * 2.0

    # \u2500\u2500 Dimension 2: Import relation matching \u2500\u2500
    imports = set(entry.get("imports", []))
    for term in prompt_terms:
        for imp in imports:
            if term in imp.lower():
                score += 1.5
                break

    # \u2500\u2500 Dimension 3: Route matching \u2500\u2500
    routes = entry.get("routes", [])
    for route in routes:
        route_path = str(route.get("path", "")).lower()
        handler = str(route.get("handler", "")).lower()
        for term in prompt_terms:
            if term in route_path or term in handler:
                score += 4.0
                break

    # \u2500\u2500 Dimension 4: Call graph expansion \u2500\u2500
    call_graph = entry.get("call_graph", {})
    all_callees: set[str] = set()
    for callees in call_graph.values():
        all_callees.update(c.lower() for c in callees)
    for term in prompt_terms:
        if term in all_callees:
            score += 2.0

    # \u2500\u2500 Dimension 5: Recent edit bonus \u2500\u2500
    if entry.get("path") in recent_changes:
        score += 3.0

    # \u2500\u2500 Dimension 6: Conversation context symbol matching \u2500\u2500
    entry_symbols = {
        s.get("name", "").lower()
        for s in entry.get("symbols", [])
        if isinstance(s, dict)
    }
    for sym in conversation_symbols:
        if sym in entry_symbols:
            score += 2.0

    # \u2500\u2500 Role weighting \u2500\u2500
    role = entry.get("role", "")
    if role == "entry_point":
        score += 1.5
    elif role == "test":
        score += 0.5

    # \u2500\u2500 Length normalization \u2500\u2500
    loc = max(entry.get("loc", 1), 1)
    score = score / math.log2(max(loc, 2))

    return score


def _extract_conversation_symbols(
    conversation_summary: str, execution_summary: str
) -> set[str]:
    """Extract symbol names from conversation context."""
    text = f"{conversation_summary} {execution_summary}".lower()
    return {
        token
        for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text)
        if len(token) >= 3
    }


def _select_relevant_files(
    prompt: str,
    index_data: dict[str, Any],
    execution_plan: dict[str, Any] | None,
    recent_changes: set[str] | None = None,
    conversation_symbols: set[str] | None = None,
) -> list[str]:
    """Multi-dimensional file relevance scoring."""
    entries = index_data.get("entries") if isinstance(index_data.get("entries"), dict) else {}
    prompt_terms = _prompt_terms(prompt)
    recent = recent_changes or set()
    conv_symbols = conversation_symbols or set()

    if not entries:
        return _fallback_files(index_data)

    # Score each file
    scored: list[tuple[float, str]] = []
    for path, entry in entries.items():
        s = _score_file(entry, prompt_terms, recent, conv_symbols, entries)
        if s > 0:
            scored.append((s, path))

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [path for _, path in scored[:10]]

    # Fallback to entry_points + recently_modified if no matches
    if not selected:
        selected = _fallback_files(index_data)

    # Augment from execution_plan
    if execution_plan:
        selected = _augment_from_plan(selected, entries, execution_plan)

    return _unique(selected)[:12]


def _fallback_files(index_data: dict[str, Any]) -> list[str]:
    """Fallback when no files match the prompt terms."""
    result: list[str] = []
    result.extend(str(path) for path in index_data.get("entry_points", [])[:5])
    result.extend(str(path) for path, _ in index_data.get("recently_modified", [])[:5])
    return result


def _augment_from_plan(
    selected: list[str],
    entries: dict[str, Any],
    execution_plan: dict[str, Any],
) -> list[str]:
    """Add files implied by execution plan stages."""
    result = list(selected)
    for stage in execution_plan.get("stages", [])[:4]:
        if not isinstance(stage, dict):
            continue
        for capability in stage.get("capabilities", []):
            text = str(capability)
            if "frontend" in text:
                result.extend(
                    path
                    for path in entries
                    if Path(path).suffix in {".js", ".jsx", ".ts", ".tsx", ".css"}
                )
            if "delivery" in text or "test" in text:
                result.extend(
                    path
                    for path, entry in entries.items()
                    if entry.get("role") == "test"
                )
    return result


def _recent_changes(workspace: Path, index_data: dict[str, Any]) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        changed = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if changed:
            return changed[:12]
    except Exception:
        pass
    return [str(path) for path, _ in index_data.get("recently_modified", [])[:8]]


def _file_outlines(index_data: dict[str, Any], files: list[str]) -> list[dict[str, Any]]:
    entries = index_data.get("entries") if isinstance(index_data.get("entries"), dict) else {}
    outlines: list[dict[str, Any]] = []
    for path in files[:12]:
        entry = entries.get(path)
        if not isinstance(entry, dict):
            continue
        outlines.append(
            {
                "path": path,
                "role": entry.get("role", "source"),
                "language": entry.get("language", "text"),
                "loc": entry.get("loc", 0),
                "symbols": entry.get("symbols", [])[:10],
                "imports": entry.get("imports", [])[:10],
            }
        )
    return outlines


def _symbol_names(file_outlines: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in file_outlines:
        for symbol in item.get("symbols", []):
            if isinstance(symbol, dict) and symbol.get("name"):
                names.append(str(symbol["name"]))
    return _unique(names)[:30]


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _workspace_summary(workspace: Path) -> dict[str, Any]:
    """Minimal workspace summary without heavy imports."""
    summary: dict[str, Any] = {"path": str(workspace)}
    try:
        from src.api.services.workspace_service import build_workspace_health
        health = build_workspace_health(str(workspace))
        summary.update(health)
    except Exception:
        pass
    return summary


def _read_project_index(workspace: Path) -> dict[str, Any]:
    import json
    idx_path = workspace / ".nanocursor" / "project_index.json"
    if idx_path.exists():
        try:
            return json.loads(idx_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}
