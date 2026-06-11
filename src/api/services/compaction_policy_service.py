"""Policy decisions for context compaction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.api.services.context_ledger_service import ContextLedger


CompactionLevel = Literal["none", "soft", "hard", "emergency"]


class CompactionAction(BaseModel):
    action: str
    title: str
    target_categories: list[str] = Field(default_factory=list)
    expected_reduction_tokens: int = 0
    reason: str = ""


class CompactionDecision(BaseModel):
    should_compact: bool
    level: CompactionLevel
    reason: str
    target_reduction_tokens: int = 0
    actions: list[CompactionAction] = Field(default_factory=list)


def decide_compaction(ledger: ContextLedger) -> CompactionDecision:
    if ledger.status in {"ok", "watch"}:
        return CompactionDecision(
            should_compact=False,
            level="none",
            reason=f"context usage is {ledger.usage_ratio:.1%}; compaction is not required",
        )

    level = _level_for_status(ledger.status)
    target_ratio = {"soft": 0.65, "hard": 0.70, "emergency": 0.72}[level]
    target_tokens = max(ledger.input_tokens - int(ledger.usable_input_tokens * target_ratio), 1)
    compactible_tokens = sum(section.tokens for section in ledger.sections if section.compactible)
    target_reduction = min(target_tokens, compactible_tokens)
    actions = _actions_for_level(level, target_reduction)
    return CompactionDecision(
        should_compact=True,
        level=level,
        reason=f"context usage is {ledger.usage_ratio:.1%}; {level} compaction is required",
        target_reduction_tokens=target_reduction,
        actions=actions,
    )


def _level_for_status(status: str) -> Literal["soft", "hard", "emergency"]:
    if status == "soft_compact":
        return "soft"
    if status == "hard_compact":
        return "hard"
    return "emergency"


def _actions_for_level(level: Literal["soft", "hard", "emergency"], target: int) -> list[CompactionAction]:
    base_share = max(target // 3, 1)
    actions = [
        CompactionAction(
            action="clear_old_tool_results",
            title="Clear old tool outputs",
            target_categories=["tool", "debug"],
            expected_reduction_tokens=base_share,
            reason="Old tool output is usually reproducible and noisy.",
        ),
        CompactionAction(
            action="summarize_agent_activity",
            title="Summarize finished agent activity",
            target_categories=["history", "run"],
            expected_reduction_tokens=base_share,
            reason="Completed agent chatter can be collapsed into a short status summary.",
        ),
    ]
    if level in {"hard", "emergency"}:
        actions.extend(
            [
                CompactionAction(
                    action="summarize_old_messages",
                    title="Summarize old conversation turns",
                    target_categories=["history", "memory"],
                    expected_reduction_tokens=base_share,
                    reason="Long historical turns should be converted to stable summaries.",
                ),
                CompactionAction(
                    action="refresh_execution_summary",
                    title="Refresh execution summary",
                    target_categories=["run"],
                    expected_reduction_tokens=max(target // 4, 1),
                    reason="Run-level state should replace repeated step details.",
                ),
                CompactionAction(
                    action="collapse_old_diff",
                    title="Collapse old diff evidence",
                    target_categories=["diff"],
                    expected_reduction_tokens=max(target // 4, 1),
                    reason="Old diffs can be represented by changed file paths and intent.",
                ),
            ]
        )
    if level == "emergency":
        actions.extend(
            [
                CompactionAction(
                    action="trim_selected_files",
                    title="Trim selected file excerpts",
                    target_categories=["files", "project"],
                    expected_reduction_tokens=max(target // 3, 1),
                    reason="Emergency pressure requires replacing file details with outlines.",
                ),
                CompactionAction(
                    action="compact_mcp_schema",
                    title="Compact MCP schema",
                    target_categories=["skills", "mcp"],
                    expected_reduction_tokens=max(target // 6, 1),
                    reason="Tool schemas are useful but rarely need full repetition.",
                ),
            ]
        )
    return actions
