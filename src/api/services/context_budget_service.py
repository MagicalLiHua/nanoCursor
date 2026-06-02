"""Context budget allocation and trimming helpers."""

from __future__ import annotations

from typing import Any

from src.agent.context_pack import ContextPack


DEFAULT_BUDGET_RATIOS = {
    "task": 0.08,
    "plan": 0.12,
    "workspace": 0.08,
    "file_outlines": 0.20,
    "snippets": 0.25,
    "recent_changes": 0.10,
    "failures": 0.08,
    "preferences_skills": 0.05,
    "reserved": 0.04,
}

STRATEGY_BUDGET_OVERRIDES = {
    "analysis_only": {
        "file_outlines": 0.28,
        "snippets": 0.12,
        "recent_changes": 0.08,
        "reserved": 0.07,
    },
    "docs_only": {
        "file_outlines": 0.18,
        "snippets": 0.18,
        "preferences_skills": 0.08,
        "reserved": 0.08,
    },
    "bug_fix": {
        "recent_changes": 0.16,
        "failures": 0.14,
        "snippets": 0.23,
        "reserved": 0.02,
    },
    "refactor": {
        "file_outlines": 0.27,
        "snippets": 0.22,
        "failures": 0.06,
        "reserved": 0.02,
    },
    "feature_delivery": {
        "plan": 0.15,
        "snippets": 0.26,
        "file_outlines": 0.18,
        "reserved": 0.02,
    },
}

PROTECTED_CONTEXT_FIELDS = {
    "task_summary": "P0 user_request",
    "current_plan": "P0 active_plan",
}


def allocate_context_budget(strategy: str, max_tokens: int = 12000) -> dict[str, Any]:
    """Allocate a token budget across context sections."""
    ratios = dict(DEFAULT_BUDGET_RATIOS)
    ratios.update(STRATEGY_BUDGET_OVERRIDES.get(strategy, {}))
    total_ratio = sum(ratios.values()) or 1.0
    sections = {
        key: int(max_tokens * (value / total_ratio))
        for key, value in ratios.items()
    }
    return {
        "strategy": strategy,
        "max_tokens": max_tokens,
        "sections": sections,
    }


def trim_context_pack(pack: ContextPack, budget: dict[str, Any]) -> ContextPack:
    """Trim selected file metadata to fit the rough outline budget.

    This intentionally does not mutate file contents because the current
    ContextPack stores outlines, not full snippets. The function still gives
    the runtime a deterministic place to enforce limits as the pack grows.
    """
    sections = budget.get("sections") if isinstance(budget.get("sections"), dict) else {}
    outline_budget = int(sections.get("file_outlines", 2400))
    max_outline_items = max(4, min(16, outline_budget // 180))
    max_selected_items = max(6, min(24, outline_budget // 100))

    original_selected = list(pack.selected_files)
    original_outlines = list(pack.file_outlines)
    included_files = original_selected[:max_selected_items]
    trimmed_files = original_selected[max_selected_items:]
    outline_paths = {
        str(item.get("path"))
        for item in included_files[:max_outline_items]
        if isinstance(item, dict) and item.get("path")
    }

    for index, item in enumerate(included_files, start=1):
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        item["budget_rank"] = index
        item["budget_decision"] = "included"
        item["budget_reason"] = "fits selected-file budget"
        if path and path not in outline_paths:
            item["budget_reason"] = "metadata included; outline omitted by outline budget"
    for index, item in enumerate(trimmed_files, start=max_selected_items + 1):
        if not isinstance(item, dict):
            continue
        item["budget_rank"] = index
        item["budget_decision"] = "trimmed"
        item["budget_reason"] = "trimmed after selected-file budget was exhausted"

    pack.selected_files = included_files
    pack.relevant_files = [str(item.get("path")) for item in pack.selected_files if item.get("path")]
    if not pack.relevant_files:
        pack.relevant_files = pack.relevant_files[:max_selected_items]
    included_outlines = original_outlines[:max_outline_items]
    trimmed_outlines = original_outlines[max_outline_items:]
    pack.file_outlines = included_outlines
    pack.omitted = _build_omitted_context(trimmed_files, trimmed_outlines, max_selected_items, max_outline_items)

    debug = dict(pack.context_debug or {})
    protected_tokens = _estimate_protected_tokens(pack)
    debug["trimmed"] = {
        "selected_file_count": len(trimmed_files),
        "selected_files": [item.get("path") for item in trimmed_files[:20] if isinstance(item, dict)],
        "file_outline_count": len(trimmed_outlines),
        "file_outlines": [item.get("path") for item in trimmed_outlines[:20] if isinstance(item, dict)],
        "max_selected_items": max_selected_items,
        "max_outline_items": max_outline_items,
        "omitted_context_count": len(pack.omitted),
    }
    debug["protected_context"] = {
        "fields": list(PROTECTED_CONTEXT_FIELDS.values()),
        "estimated_tokens": protected_tokens,
        "preserved": True,
    }
    pack.context_debug = debug
    used_tokens = pack.estimate_tokens()
    pack.token_budget = {
        **budget,
        "used_tokens_estimate": used_tokens,
        "protected_tokens_estimate": protected_tokens,
    }
    pack.budget_report = {
        "strategy": budget.get("strategy"),
        "max_tokens": budget.get("max_tokens", 12000),
        "section_budgets": sections,
        "used_tokens_estimate": used_tokens,
        "protected_tokens_estimate": protected_tokens,
        "protected_sections": list(PROTECTED_CONTEXT_FIELDS.values()),
        "utilization": round(used_tokens / max(int(budget.get("max_tokens", 12000) or 12000), 1), 4),
        "selected_file_budget": max_selected_items,
        "outline_budget": max_outline_items,
        "included_file_count": len(included_files),
        "trimmed_file_count": len(trimmed_files),
        "included_outline_count": len(included_outlines),
        "trimmed_outline_count": len(trimmed_outlines),
        "omitted_context_count": len(pack.omitted),
        "omitted": pack.omitted[:20],
        "files": [
            {
                "path": item.get("path"),
                "decision": item.get("budget_decision", "included"),
                "mode": item.get("mode", "outline"),
                "estimated_tokens": item.get("token_estimate", 0),
                "rank": item.get("budget_rank"),
                "reason": item.get("budget_reason", ""),
            }
            for item in included_files + trimmed_files[:20]
            if isinstance(item, dict)
        ],
    }
    return pack


def _build_omitted_context(
    trimmed_files: list[Any],
    trimmed_outlines: list[Any],
    max_selected_items: int,
    max_outline_items: int,
) -> list[dict[str, Any]]:
    """Return compact audit records for context omitted by budget trimming."""
    omitted: list[dict[str, Any]] = []
    for index, item in enumerate(trimmed_files, start=max_selected_items + 1):
        if not isinstance(item, dict):
            continue
        omitted.append({
            "kind": "selected_file",
            "path": item.get("path"),
            "rank": index,
            "score": item.get("relevance_score", 0),
            "reason": "trimmed after selected-file budget was exhausted",
            "reasons": item.get("reasons") if isinstance(item.get("reasons"), list) else [],
        })
    for index, item in enumerate(trimmed_outlines, start=max_outline_items + 1):
        if not isinstance(item, dict):
            continue
        omitted.append({
            "kind": "file_outline",
            "path": item.get("path"),
            "rank": index,
            "reason": "outline omitted after outline budget was exhausted",
            "symbols": len(item.get("symbols", [])) if isinstance(item.get("symbols"), list) else 0,
        })
    return omitted


def _estimate_protected_tokens(pack: ContextPack) -> int:
    """Estimate non-trimmable P0 context usage."""
    protected_chars = len(pack.task_summary or "")
    protected_chars += len(str(pack.current_plan or ""))
    return protected_chars // 3
