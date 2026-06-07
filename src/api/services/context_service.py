"""Build structured ContextPack from workspace state."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from src.agent.context_pack import ContextPack
from src.api.services.context_budget_service import allocate_context_budget, trim_context_pack
from src.api.services.context_recovery_service import (
    build_compact_recovery_context,
    build_retry_failure_context,
    load_run_retry_context,
    merge_failure_context_items,
)
from src.api.services.file_outline_service import build_file_outlines_cache, select_cached_outlines
from src.api.services.memory_selection_service import select_memories
from src.api.services.skill_registry_service import get_skill, preview_skill_selection
from src.infra import config as config_module
from src.indexer.indexer import get_project_index
from src.runtime.git_runner import run_git


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
    turn_context: dict[str, Any] | None = None,
) -> ContextPack:
    """Build a structured context pack from workspace and execution state."""
    workspace = _workspace(workspace_dir)
    pack = ContextPack()
    compact_turn_context = _compact_turn_context(turn_context)
    selection_prompt = _prompt_with_turn_context(prompt, compact_turn_context)

    # Task summary
    pack.task_summary = (prompt or "")[:200]
    pack.turn_context = compact_turn_context
    pack.tool_policy = _compact_tool_policy(
        execution_plan.get("tool_policy") if isinstance(execution_plan, dict) else None
    )
    pack.conversation_summary = _conversation_summary(workspace, conversation_id)
    pack.execution_summary = _execution_summary(workspace, thread_id)

    # Workspace summary
    pack.workspace_summary = _workspace_summary(workspace)

    index_data = _project_index_summary(workspace)
    recent_change_list = _recent_changes(workspace, index_data)
    conversation_symbols = _extract_conversation_symbols(
        pack.conversation_summary, pack.execution_summary
    )
    selected_file_details = _select_relevant_file_details(
        selection_prompt, index_data, execution_plan,
        recent_changes=set(recent_change_list),
        conversation_symbols=conversation_symbols,
    )
    pack.selected_files = selected_file_details
    pack.relevant_files = [item["path"] for item in selected_file_details]
    pack.selection_reasons = _selection_reason_summary(
        selected_file_details,
        prompt_terms=_prompt_terms(selection_prompt),
        recent_changes=recent_change_list,
        conversation_symbols=conversation_symbols,
    )
    pack.recent_changes = recent_change_list
    outline_cache = build_file_outlines_cache(workspace, index_data)
    pack.file_outlines = select_cached_outlines(workspace, pack.relevant_files, index_data)
    if not pack.file_outlines:
        pack.file_outlines = _file_outlines(index_data, pack.relevant_files)
    pack.symbols = _symbol_names(pack.file_outlines)

    # Recent failures from recovery
    from src.api.services.recovery_service import build_recovery_center
    recovery = build_recovery_center(thread_id, str(workspace))
    retry_context = load_run_retry_context(thread_id, str(workspace))
    failure_context = merge_failure_context_items(
        _failure_context_items(
            recovery,
            index_data,
            selected_file_details,
            recent_change_list,
        ),
        build_retry_failure_context(retry_context, index_data),
    )
    selected_file_details = _merge_failure_related_files(
        selected_file_details,
        failure_context,
        index_data,
    )
    pack.selected_files = selected_file_details
    pack.relevant_files = [item["path"] for item in selected_file_details]
    pack.file_outlines = select_cached_outlines(workspace, pack.relevant_files, index_data)
    if not pack.file_outlines:
        pack.file_outlines = _file_outlines(index_data, pack.relevant_files)
    pack.symbols = _symbol_names(pack.file_outlines)
    pack.recent_failures = failure_context[:5]
    pack.recovery_context = build_compact_recovery_context(recovery, failure_context, retry_context=retry_context)

    # Governed memory selection is scoped, explainable, and budgeted before prompt rendering.
    memory_selection = select_memories(
        str(workspace),
        prompt=selection_prompt,
        conversation_id=conversation_id,
        run_id=thread_id,
        selected_files=pack.relevant_files,
        active_task=compact_turn_context.get("active_task")
        if isinstance(compact_turn_context.get("active_task"), dict) else None,
        budget_tokens=1200,
    )
    pack.selected_memories = memory_selection.get("selected", [])
    pack.omitted_memories = memory_selection.get("omitted", [])
    pack.memory_budget = memory_selection.get("budget", {})

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

    # Selected skills are audited separately from tool permissions. A Skill may
    # suggest a working method, but it never grants tools by itself.
    if execution_plan:
        skill_audit = _skill_selection_audit(
            prompt=selection_prompt,
            team=team or [],
            workspace_dir=str(workspace),
            execution_plan=execution_plan,
        )
        pack.selected_skills = skill_audit["selected_ids"]
        pack.selected_skill_details = skill_audit["selected"]
        pack.omitted_skills = skill_audit["omitted"]
        pack.skill_budget = skill_audit["budget"]
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
    budget = allocate_context_budget(strategy, _strategy_token_budget(strategy))
    pack.context_debug = {
        "strategy": strategy,
        "prompt_terms": sorted(_prompt_terms(selection_prompt))[:30],
        "conversation_symbols": sorted(conversation_symbols)[:30],
        "recent_change_count": len(recent_change_list),
        "selected_file_count": len(pack.selected_files),
        "turn_context": {
            "step": compact_turn_context.get("step"),
            "active_task_id": (compact_turn_context.get("active_task") or {}).get("id")
            if isinstance(compact_turn_context.get("active_task"), dict) else None,
            "recent_tool_result_count": len(compact_turn_context.get("recent_tool_results", []))
            if isinstance(compact_turn_context.get("recent_tool_results"), list) else 0,
        },
        "outline_cache": {
            "schema_version": outline_cache.get("schema_version", 1),
            "outline_count": outline_cache.get("outline_count", 0),
            "generated_at": outline_cache.get("generated_at"),
        },
        "memory_inputs": {
            "conversation_summary_chars": len(pack.conversation_summary),
            "execution_summary_chars": len(pack.execution_summary),
            "current_plan_items": len(pack.current_plan),
            "user_preference_count": len(pack.user_preferences),
            "selected_memory_count": len(pack.selected_memories),
            "omitted_memory_count": len(pack.omitted_memories),
            "memory_tokens_estimate": pack.memory_budget.get("used_tokens_estimate", 0),
            "memory_selection_id": memory_selection.get("selection_id"),
        },
        "skill_inputs": {
            "selected_count": len(pack.selected_skill_details),
            "omitted_count": len(pack.omitted_skills),
            "context_budget": pack.skill_budget.get("context_budget", 0),
            "selected_ids": pack.selected_skills,
        },
        "failure_context": {
            "risk_count": recovery.get("summary", {}).get("risk_count", 0),
            "included_failure_count": len(pack.recent_failures),
            "related_file_count": len({
                path
                for failure in pack.recent_failures
                for path in failure.get("related_files", [])
            }),
            "recovery_action_count": recovery.get("summary", {}).get("action_count", 0),
            "high_priority_action_count": sum(
                1
                for action in pack.recovery_context.get("actions", [])
                if isinstance(action, dict) and action.get("priority") == "high"
            ),
            "retry_source": {
                "original_thread_id": pack.recovery_context.get("original_thread_id"),
                "retry_mode": pack.recovery_context.get("retry_mode"),
                "failed_stage_id": pack.recovery_context.get("failed_stage_id"),
            },
        },
        "selection_version": "context-pack-2",
    }
    pack = trim_context_pack(pack, budget)

    return pack


def _skill_selection_audit(
    *,
    prompt: str,
    team: list[dict[str, Any]],
    workspace_dir: str,
    execution_plan: dict[str, Any],
) -> dict[str, Any]:
    """Return selected and omitted Skill records for this context pack.

    Direct replies intentionally skip Skill injection. This prevents greetings
    and lightweight Q&A from inheriting stale code-oriented capabilities.
    """
    strategy = str(execution_plan.get("strategy") or "")
    if strategy == "lead_direct_reply":
        return {
            "selected_ids": [],
            "selected": [],
            "omitted": [],
            "budget": {
                "strategy": strategy,
                "context_budget": 0,
                "selected_count": 0,
                "omitted_count": 0,
                "skipped": "lead_direct_reply",
            },
        }

    preview = preview_skill_selection(prompt, workspace_dir, team=team, max_skills=5)
    selected: list[dict[str, Any]] = [
        _compact_skill_selection(item, source="selector")
        for item in preview.get("selected", [])
        if isinstance(item, dict) and item.get("id")
    ]
    selected_ids = [str(item["id"]) for item in selected]

    for capability in execution_plan.get("capabilities", []) or []:
        if not (isinstance(capability, str) and capability.startswith("skill.")):
            continue
        if capability in selected_ids:
            continue
        try:
            detail = get_skill(capability, workspace_dir)
        except ValueError:
            selected.append({
                "id": capability,
                "name": capability,
                "score": 0,
                "selection_reasons": ["execution plan capability, skill not installed"],
                "tool_permissions": [],
                "context_budget": 0,
                "risk": "unknown",
                "source": "execution_plan",
                "available": False,
            })
        else:
            if not detail.get("enabled", True):
                continue
            selected.append({
                "id": capability,
                "name": detail.get("name", capability),
                "score": 0,
                "selection_reasons": ["execution plan capability"],
                "tool_permissions": detail.get("tool_permissions", []),
                "context_budget": detail.get("context_budget", 0),
                "risk": detail.get("risk", "low"),
                "source": "execution_plan",
                "available": True,
            })
        selected_ids.append(capability)

    omitted = [
        _compact_skill_omission(item)
        for item in preview.get("omitted", [])
        if isinstance(item, dict) and item.get("id") not in selected_ids
    ]
    context_budget = sum(int(item.get("context_budget") or 0) for item in selected)
    return {
        "selected_ids": selected_ids,
        "selected": selected,
        "omitted": omitted[:20],
        "budget": {
            "strategy": strategy,
            "context_budget": context_budget,
            "selected_count": len(selected),
            "omitted_count": len(omitted),
        },
    }


def _compact_skill_selection(item: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or ""),
        "score": item.get("score", 0),
        "selection_reasons": [
            str(reason)[:240]
            for reason in item.get("selection_reasons", [])
            if str(reason).strip()
        ][:6],
        "tool_permissions": [
            str(permission)[:80]
            for permission in item.get("tool_permissions", [])
            if str(permission).strip()
        ][:10],
        "context_budget": int(item.get("context_budget") or 0),
        "risk": str(item.get("risk") or "low")[:80],
        "source": source,
        "available": bool(item.get("enabled", True)),
    }


def _compact_skill_omission(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or ""),
        "score": item.get("score", 0),
        "reason": str(item.get("reason") or "not selected")[:240],
        "risk": str(item.get("risk") or "low")[:80],
        "scope": str(item.get("scope") or "")[:80],
        "enabled": bool(item.get("enabled", True)),
    }


def _compact_turn_context(turn_context: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only prompt-safe, compact per-turn context signals."""
    if not isinstance(turn_context, dict):
        return {}

    active_task = turn_context.get("active_task")
    compact_task: dict[str, Any] = {}
    if isinstance(active_task, dict):
        compact_task = {
            "id": str(active_task.get("id") or "")[:120],
            "title": str(active_task.get("title") or "")[:240],
            "goal": str(active_task.get("goal") or "")[:500],
            "status": str(active_task.get("status") or "")[:80],
            "type": str(active_task.get("type") or "")[:80],
            "agent_role": str(active_task.get("agent_role") or active_task.get("agent") or "")[:80],
            "acceptance": _compact_context_items(active_task.get("acceptance"), limit=6),
            "recent_evidence": _compact_context_items(active_task.get("recent_evidence"), limit=6),
            "recent_outputs": _compact_context_items(active_task.get("recent_outputs"), limit=4),
        }
        compact_task = {key: value for key, value in compact_task.items() if value not in ("", [])}

    failed_tasks = []
    raw_failed_tasks = turn_context.get("failed_tasks")
    if isinstance(raw_failed_tasks, list):
        for task in raw_failed_tasks[:6]:
            if not isinstance(task, dict):
                continue
            compact = {
                "id": str(task.get("id") or "")[:120],
                "title": str(task.get("title") or "")[:240],
                "goal": str(task.get("goal") or "")[:500],
                "status": str(task.get("status") or "")[:80],
                "type": str(task.get("type") or "")[:80],
                "agent_role": str(task.get("agent_role") or task.get("agent") or "")[:80],
                "recent_evidence": _compact_context_items(task.get("recent_evidence"), limit=4),
                "recent_outputs": _compact_context_items(task.get("recent_outputs"), limit=3),
            }
            compact = {key: value for key, value in compact.items() if value not in ("", [])}
            if compact:
                failed_tasks.append(compact)

    tool_results = []
    raw_results = turn_context.get("recent_tool_results")
    if isinstance(raw_results, list):
        for item in raw_results[:8]:
            if not isinstance(item, dict):
                continue
            compact = {
                "type": str(item.get("type") or "")[:100],
                "title": str(item.get("title") or "")[:200],
                "agent": str(item.get("agent") or "")[:80],
                "task_id": str(item.get("task_id") or "")[:120],
                "tool": str(item.get("tool") or item.get("kind") or "")[:100],
                "target": str(item.get("target") or item.get("path") or "")[:240],
                "status": str(item.get("status") or item.get("result") or "")[:100],
                "summary": str(item.get("summary") or item.get("content") or "")[:500],
            }
            changed_files = item.get("changed_files")
            if isinstance(changed_files, list):
                compact["changed_files"] = [str(path)[:240] for path in changed_files[:8]]
            compact = {key: value for key, value in compact.items() if value}
            if compact:
                tool_results.append(compact)

    counts = turn_context.get("task_status_counts")
    if not isinstance(counts, dict):
        counts = {}

    recent_event_types = turn_context.get("recent_event_types")
    if not isinstance(recent_event_types, list):
        recent_event_types = []
    changed_files = turn_context.get("changed_files")
    if not isinstance(changed_files, list):
        changed_files = []

    result = {
        "turn_id": str(turn_context.get("turn_id") or "")[:120],
        "step": int(turn_context.get("step") or 0) if str(turn_context.get("step") or "").isdigit() else turn_context.get("step"),
        "active_task": compact_task,
        "failed_tasks": failed_tasks,
        "task_status_counts": {
            str(key)[:80]: int(value) if isinstance(value, int) else value
            for key, value in list(counts.items())[:12]
        },
        "recent_tool_results": tool_results,
        "changed_files": [str(path)[:240] for path in changed_files[:20] if path],
        "recent_event_types": [str(item)[:100] for item in recent_event_types[:12]],
    }
    return {key: value for key, value in result.items() if value not in ({}, [], "", None, 0)}


def _compact_tool_policy(tool_policy: Any) -> dict[str, Any]:
    """Keep the runtime's current action boundary compact and prompt-safe."""
    if not isinstance(tool_policy, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("mode", "risk_level", "strategy"):
        value = tool_policy.get(key)
        if value not in (None, ""):
            result[key] = str(value)[:100]
    for key in (
        "allowed_tools",
        "denied_tools",
        "approval_required",
        "approval_required_levels",
        "recommended_tools",
    ):
        value = tool_policy.get(key)
        if key == "denied_tools" and not isinstance(value, list):
            value = tool_policy.get("blocked_tools")
        if key == "approval_required" and not isinstance(value, list):
            value = tool_policy.get("requires_approval")
        if isinstance(value, list):
            result[key] = list(dict.fromkeys(str(item)[:100] for item in value if item))[:32]
    budgets = tool_policy.get("budgets")
    if isinstance(budgets, dict):
        result["budgets"] = {
            str(key)[:100]: value
            for key, value in list(budgets.items())[:16]
            if isinstance(value, (int, float, bool, str))
        }
    return {key: value for key, value in result.items() if value not in ({}, [], "", None)}


def _prompt_with_turn_context(prompt: str, turn_context: dict[str, Any]) -> str:
    """Blend the active turn into the selection prompt without changing user text."""
    parts = [prompt or ""]
    active_task = turn_context.get("active_task")
    if isinstance(active_task, dict):
        parts.extend([
            str(active_task.get("title") or ""),
            str(active_task.get("goal") or ""),
            str(active_task.get("agent_role") or ""),
        ])
        for field in ("acceptance", "recent_evidence", "recent_outputs"):
            for item in active_task.get(field, []) if isinstance(active_task.get(field), list) else []:
                if isinstance(item, dict):
                    parts.extend([
                        str(item.get("title") or ""),
                        str(item.get("description") or ""),
                        str(item.get("content") or ""),
                        str(item.get("path") or ""),
                    ])
    for task in turn_context.get("failed_tasks", []) if isinstance(turn_context.get("failed_tasks"), list) else []:
        if not isinstance(task, dict):
            continue
        parts.extend([
            str(task.get("title") or ""),
            str(task.get("goal") or ""),
            str(task.get("status") or ""),
        ])
        for field in ("recent_evidence", "recent_outputs"):
            for item in task.get(field, []) if isinstance(task.get(field), list) else []:
                if isinstance(item, dict):
                    parts.extend([
                        str(item.get("content") or ""),
                        str(item.get("path") or ""),
                        " ".join(str(path) for path in item.get("changed_files", []) if path)
                        if isinstance(item.get("changed_files"), list) else "",
                    ])
    for item in turn_context.get("recent_tool_results", []) if isinstance(turn_context.get("recent_tool_results"), list) else []:
        if isinstance(item, dict):
            parts.extend([
                str(item.get("tool") or ""),
                str(item.get("target") or ""),
                str(item.get("summary") or ""),
                " ".join(str(path) for path in item.get("changed_files", []) if path)
                if isinstance(item.get("changed_files"), list) else "",
            ])
    parts.extend(str(path) for path in turn_context.get("changed_files", []) if path)
    return " ".join(part for part in parts if str(part or "").strip())


def _compact_context_items(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[-limit:]:
        if not isinstance(item, dict):
            continue
        compact = {
            "id": str(item.get("id") or item.get("event_id") or "")[:120],
            "kind": str(item.get("kind") or item.get("type") or "")[:100],
            "status": str(item.get("status") or "")[:100],
            "title": str(item.get("title") or "")[:240],
            "description": str(item.get("description") or "")[:500],
            "content": str(item.get("content") or item.get("summary") or "")[:500],
            "path": str(item.get("path") or "")[:240],
            "tool": str(item.get("tool") or "")[:100],
        }
        changed_files = item.get("changed_files")
        if isinstance(changed_files, list):
            compact["changed_files"] = [str(path)[:240] for path in changed_files[:8] if path]
        compact = {key: value for key, value in compact.items() if value not in ("", [])}
        if compact:
            result.append(compact)
    return result


def _selection_reason_summary(
    selected_files: list[dict[str, Any]],
    *,
    prompt_terms: set[str],
    recent_changes: list[str],
    conversation_symbols: set[str],
) -> list[str]:
    """Summarize why this context pack was built this way."""
    summary: list[str] = []
    if prompt_terms:
        summary.append(
            "prompt_terms: " + ", ".join(sorted(prompt_terms)[:12])
        )
    if recent_changes:
        summary.append(
            "recent_changes: " + ", ".join(str(path) for path in recent_changes[:8])
        )
    if conversation_symbols:
        summary.append(
            "conversation_symbols: " + ", ".join(sorted(conversation_symbols)[:10])
        )
    if selected_files:
        top = []
        for item in selected_files[:5]:
            path = item.get("path", "")
            score = item.get("relevance_score", 0)
            reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
            top_reason = str(reasons[0]) if reasons else "fallback"
            top.append(f"{path} score={score} ({top_reason})")
        summary.append("top_selected_files: " + " | ".join(top))
    if not summary:
        summary.append("fallback: no strong prompt or workspace signals were available")
    return summary[:8]


def _failure_context_items(
    recovery: dict[str, Any],
    index_data: dict[str, Any],
    selected_files: list[dict[str, Any]],
    recent_changes: list[str],
) -> list[dict[str, Any]]:
    """Convert recovery risks into compact context items with file relations."""
    risks = recovery.get("risks") if isinstance(recovery.get("risks"), list) else []
    actions = recovery.get("actions") if isinstance(recovery.get("actions"), list) else []
    entries = index_data.get("entries") if isinstance(index_data.get("entries"), dict) else {}
    known_paths = {str(path) for path in entries}
    selected_paths = {str(item.get("path")) for item in selected_files if isinstance(item, dict) and item.get("path")}
    recent_paths = {str(path) for path in recent_changes}
    result: list[dict[str, Any]] = []

    for risk in risks[:12]:
        if not isinstance(risk, dict):
            continue
        evidence = risk.get("evidence") if isinstance(risk.get("evidence"), dict) else {}
        text_blob = _jsonish_text(risk)
        related_files = _extract_file_mentions(text_blob, known_paths)
        for path in sorted(selected_paths | recent_paths):
            if path and path in text_blob and path not in related_files:
                related_files.append(path)
        related_files = _unique(related_files)[:8]

        related_tasks = _unique([
            str(value)
            for value in (
                evidence.get("task_id"),
                evidence.get("stage_id"),
                risk.get("task_id"),
            )
            if value
        ])
        source_events = _unique([
            str(value)
            for value in (
                evidence.get("event_id"),
                risk.get("event_id"),
            )
            if value
        ])
        category = str(
            evidence.get("failure_category")
            or evidence.get("category")
            or risk.get("category")
            or "unknown"
        )
        confidence = _confidence_score(evidence.get("failure_confidence"))
        if related_files and confidence < 0.5:
            confidence = 0.65
        elif not related_files and confidence <= 0:
            confidence = 0.25

        result.append(
            {
                "id": str(risk.get("id") or ""),
                "category": category,
                "severity": str(risk.get("severity") or "medium"),
                "summary": str(risk.get("title") or evidence.get("failure_summary") or "")[:160],
                "detail": str(risk.get("detail") or "")[:360],
                "related_files": related_files,
                "related_tasks": related_tasks,
                "source_events": source_events,
                "confidence": round(confidence, 3),
                "recovery_actions": [
                    str(action.get("id"))
                    for action in actions[:5]
                    if isinstance(action, dict) and action.get("id")
                ],
            }
        )
    return result


def _merge_failure_related_files(
    selected_files: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    index_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Promote files mentioned by failures so recovery context affects selection."""
    entries = index_data.get("entries") if isinstance(index_data.get("entries"), dict) else {}
    result = [dict(item) for item in selected_files]
    by_path = {str(item.get("path")): item for item in result if item.get("path")}

    for failure in failures:
        if not isinstance(failure, dict):
            continue
        summary = str(failure.get("summary") or failure.get("category") or "recent failure")
        for path in failure.get("related_files", [])[:6]:
            path = str(path)
            if not path:
                continue
            reason = f"recent failure related: {summary[:80]}"
            if path in by_path:
                item = by_path[path]
                item.setdefault("reasons", []).append(reason)
                item["reasons"] = _unique(item["reasons"])
                item["relevance_score"] = round(float(item.get("relevance_score", 0)) + 1.25, 4)
                continue
            if path in entries:
                item = _selected_file_item(path, entries.get(path, {}), 2.5, [reason], "outline")
                result.append(item)
                by_path[path] = item

    result.sort(key=lambda item: float(item.get("relevance_score", 0)), reverse=True)
    return result[:24]


def _extract_file_mentions(text: str, known_paths: set[str]) -> list[str]:
    """Extract workspace-relative file mentions from risk text."""
    mentions: list[str] = []
    lowered = text.lower()
    for path in sorted(known_paths):
        if path and _path_token_present(path.lower(), lowered):
            mentions.append(path)

    basename_index: dict[str, list[str]] = {}
    for path in known_paths:
        basename_index.setdefault(Path(path).name.lower(), []).append(path)

    raw_matches = re.findall(
        r"[\w./\\-]+\.(?:py|js|jsx|ts|tsx|css|md|json|toml|yaml|yml|txt|html|vue)",
        text,
    )
    for raw in raw_matches:
        candidate = raw.strip(".,;:()[]{}'\"`")
        candidate = candidate.replace("\\", "/")
        candidate = candidate.lstrip("./")
        if candidate in known_paths:
            mentions.append(candidate)
            continue
        matches = basename_index.get(Path(candidate).name.lower(), [])
        if len(matches) == 1:
            mentions.append(matches[0])

    return _unique(mentions)


def _path_token_present(path: str, text: str) -> bool:
    pattern = r"(?<![\w./\\-])" + re.escape(path) + r"(?![\w./\\-])"
    return re.search(pattern, text) is not None


def _jsonish_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _confidence_score(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().lower()
    if text in {"high", "certain", "strong"}:
        return 0.85
    if text in {"medium", "moderate"}:
        return 0.6
    if text in {"low", "weak"}:
        return 0.35
    try:
        return float(text)
    except ValueError:
        return 0.0


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
        return _bounded_summary(summary, 1200)
    records = data.get("run_records") if isinstance(data.get("run_records"), list) else []
    parts = []
    for record in records[-5:]:
        if not isinstance(record, dict):
            continue
        parts.append(
            f"Run#{record.get('run_index', '?')} {record.get('status', 'unknown')}: "
            f"{record.get('prompt', '')[:80]} -> {record.get('summary', '')[:160]}"
        )
    return _bounded_summary("；".join(parts), 1200)


def _bounded_summary(text: str, limit: int) -> str:
    """Preserve stable context and the newest risks when a summary is long."""
    compact = str(text or "").strip()
    if len(compact) <= limit:
        return compact
    marker = "\n...[中间摘要已压缩]...\n"
    available = max(limit - len(marker), 2)
    head_size = max(1, int(available * 0.6))
    tail_size = max(1, available - head_size)
    return f"{compact[:head_size]}{marker}{compact[-tail_size:]}"[:limit]


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


def _score_file_detail(
    entry: dict[str, Any],
    prompt_terms: set[str],
    recent_changes: set[str],
    conversation_symbols: set[str],
    all_entries: dict[str, Any],
) -> tuple[float, list[str]]:
    """Multi-dimensional file relevance scoring."""
    score = 0.0
    reasons: list[str] = []

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
            reasons.append(f"prompt term matched: {term}")

    # \u2500\u2500 Dimension 2: Import relation matching \u2500\u2500
    imports = set(entry.get("imports", []))
    for term in prompt_terms:
        for imp in imports:
            if term in imp.lower():
                score += 1.5
                reasons.append(f"import matched: {imp}")
                break

    # \u2500\u2500 Dimension 3: Route matching \u2500\u2500
    routes = entry.get("routes", [])
    for route in routes:
        route_path = str(route.get("path", "")).lower()
        handler = str(route.get("handler", "")).lower()
        for term in prompt_terms:
            if term in route_path or term in handler:
                score += 4.0
                reasons.append(f"route matched: {route_path or handler}")
                break

    # \u2500\u2500 Dimension 4: Call graph expansion \u2500\u2500
    call_graph = entry.get("call_graph", {})
    all_callees: set[str] = set()
    for callees in call_graph.values():
        all_callees.update(c.lower() for c in callees)
    for term in prompt_terms:
        if term in all_callees:
            score += 2.0
            reasons.append(f"call graph matched: {term}")

    # \u2500\u2500 Dimension 5: Recent edit bonus \u2500\u2500
    if entry.get("path") in recent_changes:
        score += 3.0
        reasons.append("recently changed")

    # \u2500\u2500 Dimension 6: Conversation context symbol matching \u2500\u2500
    entry_symbols = {
        s.get("name", "").lower()
        for s in entry.get("symbols", [])
        if isinstance(s, dict)
    }
    for sym in conversation_symbols:
        if sym in entry_symbols:
            score += 2.0
            reasons.append(f"conversation symbol matched: {sym}")

    # \u2500\u2500 Role weighting \u2500\u2500
    role = entry.get("role", "")
    if role == "entry_point":
        score += 1.5
        reasons.append("entry point")
    elif role == "test":
        score += 0.5
        reasons.append("test file")

    # \u2500\u2500 Length normalization \u2500\u2500
    loc = max(entry.get("loc", 1), 1)
    score = score / math.log2(max(loc, 2))

    return score, _unique(reasons)[:8]


def _score_file(
    entry: dict[str, Any],
    prompt_terms: set[str],
    recent_changes: set[str],
    conversation_symbols: set[str],
    all_entries: dict[str, Any],
) -> float:
    return _score_file_detail(
        entry, prompt_terms, recent_changes, conversation_symbols, all_entries
    )[0]


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


def _select_relevant_file_details(
    prompt: str,
    index_data: dict[str, Any],
    execution_plan: dict[str, Any] | None,
    recent_changes: set[str] | None = None,
    conversation_symbols: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return selected files with scores and reasons."""
    entries = index_data.get("entries") if isinstance(index_data.get("entries"), dict) else {}
    prompt_terms = _prompt_terms(prompt)
    recent = recent_changes or set()
    conv_symbols = conversation_symbols or set()

    if not entries:
        return [
            _selected_file_item(path, {}, 0.1, ["fallback file"], "outline")
            for path in _fallback_files(index_data)
        ]

    scored: list[dict[str, Any]] = []
    for path, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        entry.setdefault("path", path)
        score, reasons = _score_file_detail(entry, prompt_terms, recent, conv_symbols, entries)
        if score > 0:
            scored.append(_selected_file_item(path, entry, score, reasons, _selection_mode(entry, score)))

    scored.sort(key=lambda item: float(item.get("relevance_score", 0)), reverse=True)

    if not scored:
        scored = [
            _selected_file_item(path, entries.get(path, {}), 0.1, ["fallback: entry point or recent file"], "outline")
            for path in _fallback_files(index_data)
        ]

    if execution_plan:
        scored = _augment_details_from_plan(scored, entries, execution_plan)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in scored:
        path = str(item.get("path", ""))
        if not path or path in seen:
            continue
        seen.add(path)
        deduped.append(item)
    return deduped[:24]


def _selected_file_item(
    path: str,
    entry: dict[str, Any],
    score: float,
    reasons: list[str],
    mode: str,
) -> dict[str, Any]:
    symbols = [
        str(sym.get("name"))
        for sym in entry.get("symbols", [])
        if isinstance(sym, dict) and sym.get("name")
    ]
    loc = int(entry.get("loc", 0) or 0)
    token_estimate = max(80, min(3000, loc * 8))
    return {
        "path": path,
        "role": entry.get("role", "unknown"),
        "language": entry.get("language", "text"),
        "relevance_score": round(float(score), 4),
        "reasons": _unique(reasons) or ["selected by fallback"],
        "mode": mode,
        "token_estimate": token_estimate,
        "symbols": symbols[:20],
        "related_tasks": [],
    }


def _selection_mode(entry: dict[str, Any], score: float) -> str:
    loc = int(entry.get("loc", 0) or 0)
    if score >= 6 and loc <= 220:
        return "full"
    if score >= 2:
        return "snippet"
    return "outline"


def _augment_details_from_plan(
    selected: list[dict[str, Any]],
    entries: dict[str, Any],
    execution_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    result = list(selected)
    selected_paths = {str(item.get("path", "")) for item in result}

    def add_path(path: str, reason: str) -> None:
        if path in selected_paths:
            for item in result:
                if item.get("path") == path:
                    item.setdefault("reasons", []).append(reason)
                    item["reasons"] = _unique(item["reasons"])
                    item["relevance_score"] = round(float(item.get("relevance_score", 0)) + 0.75, 4)
                    break
            return
        entry = entries.get(path, {})
        result.append(_selected_file_item(path, entry, 0.75, [reason], "outline"))
        selected_paths.add(path)

    for stage in execution_plan.get("stages", [])[:6]:
        if not isinstance(stage, dict):
            continue
        for capability in stage.get("capabilities", []):
            text = str(capability)
            if "frontend" in text:
                for path in entries:
                    if Path(path).suffix in {".js", ".jsx", ".ts", ".tsx", ".css"}:
                        add_path(path, "execution plan capability: frontend")
            if "delivery" in text or "test" in text:
                for path, entry in entries.items():
                    if isinstance(entry, dict) and entry.get("role") == "test":
                        add_path(path, "execution plan requires verification")

    result.sort(key=lambda item: float(item.get("relevance_score", 0)), reverse=True)
    return result


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
    result = run_git(workspace, ["diff", "--name-only"], timeout_seconds=3)
    if result.returncode == 0:
        changed = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if changed:
            return changed[:12]
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
