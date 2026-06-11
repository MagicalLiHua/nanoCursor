"""Deterministic context compaction service."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.api.services.compaction_policy_service import CompactionDecision, decide_compaction
from src.api.services.context_compaction_settings_service import SummaryMode
from src.api.services.context_ledger_service import (
    ContextLedger,
    ContextSection,
    build_context_ledger,
    load_latest_context_ledger,
    save_context_ledger,
)
from src.api.services.model_context_registry_service import ModelContextSpec


class CompactionResult(BaseModel):
    compacted: bool
    level: str
    strategy: Literal["deterministic", "summary"] = "deterministic"
    before_tokens: int
    after_tokens: int
    reduced_tokens: int
    updated_sections: list[str] = Field(default_factory=list)
    preserved_anchors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    ledger: ContextLedger | None = None


def compact_ledger(
    ledger: ContextLedger,
    *,
    decision: CompactionDecision | None = None,
    reason: str = "policy",
) -> CompactionResult:
    decision = decision or decide_compaction(ledger)
    if not decision.should_compact:
        return CompactionResult(
            compacted=False,
            level=decision.level,
            before_tokens=ledger.input_tokens,
            after_tokens=ledger.input_tokens,
            reduced_tokens=0,
            preserved_anchors=_preserved_anchor_ids(ledger),
            warnings=[decision.reason],
            ledger=ledger,
        )

    reduction_ratio = {"soft": 0.35, "hard": 0.55, "emergency": 0.70}.get(decision.level, 0.35)
    remaining_target = max(decision.target_reduction_tokens, 0)
    updated_sections: list[str] = []
    compacted_sections: list[ContextSection] = []

    for section in sorted(ledger.sections, key=lambda item: item.priority):
        new_section = section.model_copy(deep=True)
        if section.compactible and remaining_target > 0:
            reduction = min(int(section.tokens * reduction_ratio), remaining_target, max(section.tokens - 1, 0))
            if reduction > 0:
                new_section.tokens = max(section.tokens - reduction, 1)
                new_section.detail = {
                    **new_section.detail,
                    "compacted": True,
                    "compaction_reason": reason,
                    "before_tokens": section.tokens,
                    "after_tokens": new_section.tokens,
                }
                remaining_target -= reduction
                updated_sections.append(section.id)
        compacted_sections.append(new_section)

    spec = ModelContextSpec(
        provider=ledger.provider,
        model=ledger.model,
        context_window=ledger.context_window,
        max_output_tokens=ledger.max_output_tokens,
        source="ledger",
    )
    compacted_ledger = build_context_ledger(
        compacted_sections,
        spec,
        conversation_id=ledger.conversation_id,
        run_id=ledger.run_id,
        turn_id=ledger.turn_id,
        reserved_output_tokens=ledger.reserved_output_tokens,
    )
    reduced = max(ledger.input_tokens - compacted_ledger.input_tokens, 0)
    warnings = []
    if decision.target_reduction_tokens and reduced < decision.target_reduction_tokens:
        warnings.append("compactible context was insufficient to reach the target reduction")
    return CompactionResult(
        compacted=bool(updated_sections),
        level=decision.level,
        before_tokens=ledger.input_tokens,
        after_tokens=compacted_ledger.input_tokens,
        reduced_tokens=reduced,
        updated_sections=updated_sections,
        preserved_anchors=_preserved_anchor_ids(ledger),
        warnings=warnings,
        ledger=compacted_ledger,
    )


def compact_context_ledger(
    workspace_dir: str | Path,
    *,
    conversation_id: str | None = None,
    run_id: str | None = None,
    level: str | None = None,
    reason: str = "manual",
    strategy: Literal["deterministic", "summary"] = "deterministic",
    summary_mode: SummaryMode = "deterministic",
) -> CompactionResult:
    ledger = load_latest_context_ledger(workspace_dir, conversation_id=conversation_id, run_id=run_id)
    if ledger is None:
        raise ValueError("Context ledger not found")
    decision = decide_compaction(ledger)
    if level and level != "none":
        decision = decision.model_copy(update={"should_compact": True, "level": level})
    if strategy == "summary":
        from src.api.services.summary_compaction_service import summary_compact_ledger

        summary_result = summary_compact_ledger(ledger, decision=decision, mode=summary_mode)
        result = CompactionResult(
            compacted=summary_result.compacted,
            level=decision.level,
            strategy="summary",
            before_tokens=summary_result.before_tokens,
            after_tokens=summary_result.after_tokens,
            reduced_tokens=summary_result.reduced_tokens,
            updated_sections=summary_result.source_section_ids,
            preserved_anchors=summary_result.preserved_anchors,
            warnings=summary_result.warnings,
            summary=summary_result.model_dump(exclude={"ledger"}),
            ledger=summary_result.ledger,
        )
    else:
        result = compact_ledger(ledger, decision=decision, reason=reason)
    if result.ledger is not None:
        save_context_ledger(result.ledger, workspace_dir)
    append_compaction_history(workspace_dir, result, conversation_id=conversation_id, run_id=run_id, reason=reason)
    return result


def append_compaction_history(
    workspace_dir: str | Path,
    result: CompactionResult,
    *,
    conversation_id: str | None = None,
    run_id: str | None = None,
    reason: str = "manual",
) -> None:
    path = _history_path(workspace_dir, conversation_id=conversation_id, run_id=run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "created_at": time.time(),
        "reason": reason,
        "result": result.model_dump(exclude={"ledger"}),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _preserved_anchor_ids(ledger: ContextLedger) -> list[str]:
    return [section.id for section in ledger.sections if not section.compactible or section.priority >= 90]


def _history_path(
    workspace_dir: str | Path,
    *,
    conversation_id: str | None,
    run_id: str | None,
) -> Path:
    workspace = Path(workspace_dir)
    if run_id:
        return workspace / ".nanocursor" / "runs" / run_id / "compaction_history.jsonl"
    if conversation_id:
        return workspace / ".nanocursor" / "conversations" / conversation_id / "compaction_history.jsonl"
    return workspace / ".nanocursor" / "context" / "compaction_history.jsonl"
